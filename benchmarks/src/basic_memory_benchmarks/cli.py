"""CLI entrypoint for benchmark operations."""

from __future__ import annotations

import json
import math
import shutil
import uuid
from functools import partial
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console

from basic_memory_benchmarks.agent_tasks.driver import run_agent_tasks
from basic_memory_benchmarks.agent_tasks.manifest import load_task_manifest
from basic_memory_benchmarks.agent_tasks.models import AgentBudget, AgentTasksConfig
from basic_memory_benchmarks.agent_tasks.spec import JudgeRubric
from basic_memory_benchmarks.agent_tasks.surfaces import SURFACES
from basic_memory_benchmarks.agent_tasks.tasks import select_tasks
from basic_memory_benchmarks.concurrent_write import (
    ConcurrentWriteConfig,
    run_concurrent_write,
)
from basic_memory_benchmarks.converters.beam_to_corpus import convert_beam_to_corpus
from basic_memory_benchmarks.converters.convomem_to_corpus import convert_convomem_to_corpus
from basic_memory_benchmarks.converters.locomo_to_corpus import convert_locomo_to_corpus
from basic_memory_benchmarks.converters.longmemeval_to_corpus import convert_longmemeval_to_corpus
from basic_memory_benchmarks.converters.xafs_to_corpus import (
    convert_xafs_to_corpus,
    sample_xafs_audit,
)
from basic_memory_benchmarks.datasets.convomem import fetch_convomem_batches
from basic_memory_benchmarks.datasets.locomo import LOCOMO_URL, fetch_locomo_dataset
from basic_memory_benchmarks.datasets.locomo_audit import fetch_locomo_audit_corrections
from basic_memory_benchmarks.datasets.longmemeval import (
    LONGMEMEVAL_S_URL,
    fetch_longmemeval_dataset,
)
from basic_memory_benchmarks.llm.tool_agent import create_tool_agent_model
from basic_memory_benchmarks.models import DatasetProvenance, RunConfig
from basic_memory_benchmarks.reporting.compare import (
    compare_provider_metric,
    load_retrieval_summary,
)
from basic_memory_benchmarks.runner import (
    run_beam_score_stage,
    run_diagnose_stage,
    run_qa_stage,
    run_rejudge_stage,
    run_retrieval,
    run_review_stage,
)
from basic_memory_benchmarks.utils import sha256_file

app = typer.Typer(help="Basic Memory benchmark suite")
console = Console()

datasets_app = typer.Typer(help="Dataset management commands")
convert_app = typer.Typer(help="Dataset conversion commands")
run_app = typer.Typer(help="Benchmark execution commands")
sample_app = typer.Typer(help="Audit sampling commands (human question-quality review)")

app.add_typer(datasets_app, name="datasets")
app.add_typer(convert_app, name="convert")
app.add_typer(run_app, name="run")
app.add_typer(sample_app, name="sample")
curate_app = typer.Typer(
    help="Agent-curated ingestion: a curator model writes notes from raw transcripts"
)
app.add_typer(curate_app, name="curate")


def parse_model_temperature(value: str) -> float | None:
    """'omit'/'none' -> None (parameter not sent); otherwise a finite float."""
    if value.strip().lower() in {"omit", "none"}:
        return None
    try:
        temperature = float(value)
    except ValueError:
        raise typer.BadParameter(
            f"--model-temperature must be a number or 'omit', got {value!r}"
        ) from None
    # nan/inf parse cleanly and pass config validation, but JSON has no
    # encoding for them: httpx raises a bare ValueError when it serializes
    # the request body. That escapes the handled transports, so a run
    # dies mid-flight after setup with no artifacts written.
    if not math.isfinite(temperature):
        raise typer.BadParameter(
            f"--model-temperature must be a finite number or 'omit', got {value!r}"
        )
    return temperature


@datasets_app.command("fetch")
def datasets_fetch(
    dataset: str = typer.Option("locomo", "--dataset"),
    output: Path | None = typer.Option(None, "--output"),
    url: str | None = typer.Option(None, "--url"),
    context_sizes: str = typer.Option(
        "10,30", "--context-sizes", help="convomem only: batch context sizes to download"
    ),
) -> None:
    if dataset == "locomo":
        resolved_output = output or Path("benchmarks/datasets/locomo/locomo10.json")
        provenance = fetch_locomo_dataset(output_path=resolved_output, url=url or LOCOMO_URL)
    elif dataset == "longmemeval-s":
        resolved_output = output or Path("benchmarks/datasets/longmemeval/longmemeval_s.json")
        provenance = fetch_longmemeval_dataset(
            output_path=resolved_output, url=url or LONGMEMEVAL_S_URL
        )
    elif dataset == "locomo-audit":
        resolved_output = output or Path("benchmarks/datasets/locomo-audit/corrections.json")
        provenance = fetch_locomo_audit_corrections(output_path=resolved_output)
    elif dataset == "convomem":
        resolved_output = output or Path("benchmarks/datasets/convomem")
        sizes = tuple(int(s.strip()) for s in context_sizes.split(",") if s.strip())
        provenance = fetch_convomem_batches(output_dir=resolved_output, context_sizes=sizes)
    else:
        raise typer.BadParameter(
            "Supported datasets: locomo, longmemeval-s, locomo-audit, convomem"
        )

    console.print(f"Downloaded {dataset} to [cyan]{resolved_output}[/cyan]")
    console.print(f"SHA256: [green]{provenance.checksum_sha256}[/green]")


@convert_app.command("locomo")
def convert_locomo(
    dataset_path: Path = typer.Option(
        Path("benchmarks/datasets/locomo/locomo10.json"), "--dataset-path"
    ),
    output_dir: Path = typer.Option(Path("benchmarks/generated/locomo"), "--output-dir"),
    max_conversations: int | None = typer.Option(None, "--max-conversations"),
    audit_corrections: Path | None = typer.Option(
        None,
        "--audit-corrections",
        help="Penfield audit corrections.json; applies corrected answers/evidence",
    ),
) -> None:
    docs_dir, queries_path, doc_count, query_count = convert_locomo_to_corpus(
        dataset_path=dataset_path,
        output_dir=output_dir,
        max_conversations=max_conversations,
        audit_corrections_path=audit_corrections,
    )
    console.print(f"Docs: [cyan]{docs_dir}[/cyan] ({doc_count})")
    console.print(f"Queries: [cyan]{queries_path}[/cyan] ({query_count})")


@convert_app.command("structure-corpus")
def convert_structure_corpus(
    input_dir: Path = typer.Option(
        ..., "--input-dir", help="Source corpus root (a flat docs dir or a grouped …/groups dir)"
    ),
    output_dir: Path = typer.Option(
        ..., "--output-dir", help="Destination root; the input layout is mirrored beneath it"
    ),
    mode: str = typer.Option(
        "augment",
        "--mode",
        help="augment: keep transcript + append structure (faithful); replace: structure only (lossy)",
    ),
    categories: str = typer.Option(
        "",
        "--categories",
        help="Grouped corpora only: comma-separated category labels to restructure (matches group-id prefix). Empty = all docs.",
    ),
    extractor: str = typer.Option(
        "claude:claude-haiku-4-5", "--extractor", help="LLM runner spec for fact extraction"
    ),
    max_workers: int = typer.Option(4, "--max-workers"),
) -> None:
    """Restructure flat conversation docs into Basic Memory observations/relations.

    Produces a structured twin of a corpus with doc ids/frontmatter preserved, so
    a flat-vs-structured run isolates the representation and recall stays
    comparable. Works on both grouped and flat corpora (layout is mirrored).
    """
    from basic_memory_benchmarks.converters.structure_corpus import (
        group_prefix_filter,
        structure_corpus,
    )
    from basic_memory_benchmarks.llm.runners import create_runner

    if mode not in ("augment", "replace"):
        raise typer.BadParameter("--mode must be 'augment' or 'replace'")
    cats = {c.strip() for c in categories.split(",") if c.strip()}
    path_filter = group_prefix_filter(cats) if cats else None
    runner = create_runner(extractor)
    output_dir.mkdir(parents=True, exist_ok=True)

    doc_count = structure_corpus(
        input_root=input_dir,
        output_root=output_dir,
        runner=runner,
        mode=mode,  # type: ignore[arg-type]
        path_filter=path_filter,
        max_workers=max_workers,
    )

    console.print(
        f"Structured ([green]{mode}[/green]): [cyan]{output_dir}[/cyan] ({doc_count} docs)"
    )
    console.print(f"Extractor: [green]{extractor}[/green]")
    if cats:
        console.print(f"Filtered to categories: {sorted(cats)}")


@convert_app.command("longmemeval")
def convert_longmemeval(
    dataset_path: Path = typer.Option(
        Path("benchmarks/datasets/longmemeval/longmemeval_s.json"), "--dataset-path"
    ),
    output_dir: Path = typer.Option(Path("benchmarks/generated/longmemeval-s"), "--output-dir"),
    max_questions: int | None = typer.Option(None, "--max-questions"),
    stratified: bool = typer.Option(
        False, "--stratified", help="Sample max-questions evenly across question types (seed 42)"
    ),
    seed: int = typer.Option(42, "--seed"),
) -> None:
    groups_dir, queries_path, doc_count, query_count = convert_longmemeval_to_corpus(
        dataset_path=dataset_path,
        output_dir=output_dir,
        max_questions=max_questions,
        stratified=stratified,
        seed=seed,
    )
    console.print(f"Groups: [cyan]{groups_dir}[/cyan] ({query_count} groups, {doc_count} docs)")
    console.print(f"Queries: [cyan]{queries_path}[/cyan] ({query_count})")


@convert_app.command("beam")
def convert_beam(
    dataset_root: Path = typer.Option(
        Path("benchmarks/datasets/beam/upstream/chats"),
        "--dataset-root",
        help="The chats/ directory of a local BEAM checkout (see datasets/beam/download.sh)",
    ),
    output_dir: Path = typer.Option(Path("benchmarks/generated/beam-100k"), "--output-dir"),
    tier: str = typer.Option("100K", "--tier", help="100K | 500K | 1M"),
    max_conversations: int | None = typer.Option(None, "--max-conversations"),
) -> None:
    """Convert a BEAM tier into grouped corpora + query manifest.

    The dataset is never vendored; fetch it first with
    `benchmarks/datasets/beam/download.sh`. Runs then pass the written
    `conversion.json` as --dataset-path so provenance pins the exact inputs.
    """
    groups_dir, queries_path, doc_count, query_count = convert_beam_to_corpus(
        dataset_root=dataset_root,
        output_dir=output_dir,
        tier=tier,
        max_conversations=max_conversations,
    )
    console.print(f"Groups: [cyan]{groups_dir}[/cyan] ({doc_count} docs)")
    console.print(f"Queries: [cyan]{queries_path}[/cyan] ({query_count})")
    console.print(f"Conversion manifest: [cyan]{output_dir / 'conversion.json'}[/cyan]")


@convert_app.command("convomem")
def convert_convomem(
    batches_dir: Path = typer.Option(Path("benchmarks/datasets/convomem"), "--batches-dir"),
    output_dir: Path = typer.Option(Path("benchmarks/generated/convomem"), "--output-dir"),
    sample_per_stratum: int = typer.Option(25, "--sample-per-stratum"),
    seed: int = typer.Option(42, "--seed"),
    context_sizes: str = typer.Option("10,30", "--context-sizes"),
) -> None:
    sizes = tuple(int(s.strip()) for s in context_sizes.split(",") if s.strip())
    groups_dir, queries_path, doc_count, query_count = convert_convomem_to_corpus(
        batches_dir=batches_dir,
        output_dir=output_dir,
        sample_per_stratum=sample_per_stratum,
        seed=seed,
        context_sizes=sizes,
    )
    console.print(f"Groups: [cyan]{groups_dir}[/cyan] ({doc_count} docs)")
    console.print(f"Queries: [cyan]{queries_path}[/cyan] ({query_count})")
    console.print(f"Sampling manifest: [cyan]{output_dir / 'sampling.json'}[/cyan]")


def _parse_personas(personas: str) -> list[str] | None:
    """Comma-separated persona ids; empty (or 'all') selects every one on disk."""
    if personas.strip().lower() in ("", "all"):
        return None
    return [item.strip() for item in personas.split(",") if item.strip()]


@convert_app.command("xafs")
def convert_xafs(
    dataset_root: Path = typer.Option(
        Path("benchmarks/datasets/xafs/upstream"),
        "--dataset-root",
        help="Local xAFS snapshot (see benchmarks/datasets/xafs/download.sh; never vendored)",
    ),
    output_dir: Path = typer.Option(Path("benchmarks/generated/xafs"), "--output-dir"),
    personas: str = typer.Option(
        "",
        "--personas",
        help="Comma-separated persona ids (dp_001,...); empty/'all' converts every "
        "downloaded persona (full ingestion is ~837MB)",
    ),
    corrections: Path | None = typer.Option(
        None,
        "--corrections",
        help="Audit corrections JSON keyed '<persona>/<qid>'; overrides gold answers "
        "or excludes questions (prompt cross-check fails loudly on drift)",
    ),
) -> None:
    """Convert xAFS personas into grouped corpora + an agent-task manifest.

    The dataset is never vendored; fetch it first with
    `benchmarks/datasets/xafs/download.sh`. Run the result with
    `run agent-tasks --task-manifest <output>/tasks.json`.
    """
    try:
        groups_dir, tasks_path, file_count, task_count = convert_xafs_to_corpus(
            dataset_root=dataset_root,
            output_dir=output_dir,
            personas=_parse_personas(personas),
            corrections_path=corrections,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"Groups: [cyan]{groups_dir}[/cyan] ({file_count} files)")
    console.print(f"Tasks: [cyan]{tasks_path}[/cyan] ({task_count})")
    console.print(f"Conversion manifest: [cyan]{output_dir / 'conversion.json'}[/cyan]")


@sample_app.command("xafs")
def sample_xafs(
    dataset_root: Path = typer.Option(
        Path("benchmarks/datasets/xafs/upstream"),
        "--dataset-root",
        help="Local xAFS snapshot (see benchmarks/datasets/xafs/download.sh)",
    ),
    personas: str = typer.Option(
        "", "--personas", help="Comma-separated persona ids; empty/'all' samples across all"
    ),
    n: int = typer.Option(20, "--n", help="Sample size (stratified across the three families)"),
    seed: int = typer.Option(42, "--seed"),
    output: Path = typer.Option(Path("benchmarks/generated/xafs-audit"), "--output"),
) -> None:
    """Extract a seeded question sample (with gold answers + source files) for human review.

    Ships the tooling for the xAFS question-quality audit; verdicts land in
    benchmarks/datasets/xafs/corrections.json and feed `convert xafs --corrections`.
    """
    try:
        sample_path, sampled = sample_xafs_audit(
            dataset_root=dataset_root,
            output_dir=output,
            personas=_parse_personas(personas),
            sample_size=n,
            seed=seed,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"Audit sample: [cyan]{sample_path}[/cyan] ({sampled} questions)")
    console.print(f"Readable form + gold files: [cyan]{output / 'sample.md'}[/cyan]")


@curate_app.command("beam")
def curate_beam_command(
    input_dir: Path = typer.Option(
        Path("benchmarks/generated/beam-100k"),
        "--input-dir",
        help="A raw `convert beam` output dir (conversion.json + queries.json)",
    ),
    output_dir: Path = typer.Option(Path("benchmarks/generated/beam-100k-curated"), "--output-dir"),
    model: str = typer.Option(
        ..., "--model", help="Curator: openai-compat:<model>@<base_url> or claude:<model>"
    ),
    model_temperature: str = typer.Option(
        "omit",
        "--model-temperature",
        help="Sampling temperature for the curator, or 'omit' (Claude 5 rejects the parameter)",
    ),
    bm_local_path: str | None = typer.Option(None, "--bm-local-path"),
    conversations: str | None = typer.Option(
        None, "--conversations", help="Comma-separated BEAM conversation ids, e.g. 1,2"
    ),
    max_conversations: int | None = typer.Option(None, "--max-conversations"),
    max_notes_per_session: int = typer.Option(8, "--max-notes-per-session"),
    settle_timeout: float = typer.Option(180.0, "--settle-timeout"),
    tool_timeout: float = typer.Option(120.0, "--tool-timeout"),
    workers: int = typer.Option(
        1, "--workers", min=1, help="Conversations curated concurrently (each has its own bm mcp)"
    ),
) -> None:
    """Curate a raw BEAM tier into knowledge notes through the write path.

    The curator sees each chat session, the running date, and the notes it
    has written; never the probes. Output has the raw layout, so
    `run retrieval`, `run qa`, and `run beam-score` run on it unchanged
    (docs/benchmarks.md 6b, curated mode).
    """
    from basic_memory_benchmarks.curation.beam import CurationConfig, curate_beam
    from basic_memory_benchmarks.llm.runners import create_runner

    temperature = parse_model_temperature(model_temperature)
    try:
        runner = create_runner(model, temperature=temperature)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    selected = (
        tuple(part.strip() for part in conversations.split(",") if part.strip())
        if conversations
        else ()
    )
    config = CurationConfig(
        input_dir=input_dir,
        output_dir=output_dir,
        model_spec=model,
        model_temperature=temperature,
        bm_local_path=bm_local_path,
        conversations=selected,
        max_conversations=max_conversations,
        max_notes_per_session=max_notes_per_session,
        settle_timeout_seconds=settle_timeout,
        tool_timeout_seconds=tool_timeout,
        workers=workers,
    )
    output = curate_beam(config, runner=runner, progress=console.print)
    manifest = json.loads((output / "conversion.json").read_text(encoding="utf-8"))
    totals = manifest["totals"]
    console.print(
        f"Curated {totals['conversations']} conversations: {totals['docs']} docs, "
        f"{totals['queries']} queries, tokens {totals['input_tokens']}+{totals['output_tokens']}"
    )
    excluded = manifest["excluded_conversations"]
    if excluded:
        console.print(f"[yellow]Excluded {len(excluded)} conversation(s)[/yellow]: {excluded}")
    console.print(f"Conversion manifest: [cyan]{output / 'conversion.json'}[/cyan]")


@run_app.command("retrieval")
def run_retrieval_command(
    providers: str = typer.Option("bm-local,mem0-local", "--providers"),
    dataset_id: str = typer.Option("locomo", "--dataset-id"),
    dataset_path: Path = typer.Option(
        Path("benchmarks/datasets/locomo/locomo10.json"), "--dataset-path"
    ),
    corpus_dir: Path = typer.Option(Path("benchmarks/generated/locomo/docs"), "--corpus-dir"),
    queries_path: Path = typer.Option(
        Path("benchmarks/generated/locomo/queries.json"), "--queries-path"
    ),
    output_root: Path = typer.Option(Path("benchmarks/runs"), "--output-root"),
    run_id: str | None = typer.Option(None, "--run-id"),
    top_k: int = typer.Option(10, "--top-k"),
    bm_source: str = typer.Option(
        "github:basicmachines-co/basic-memory@main",
        "--bm-source",
    ),
    bm_local_path: str | None = typer.Option(None, "--bm-local-path"),
    allow_provider_skip: bool = typer.Option(True, "--allow-provider-skip/--strict-providers"),
) -> None:
    resolved_run_id = run_id or uuid.uuid4().hex[:12]
    provider_list = [item.strip() for item in providers.split(",") if item.strip()]

    if not dataset_path.exists():
        raise typer.BadParameter(f"Dataset path not found: {dataset_path}")
    if not corpus_dir.exists():
        raise typer.BadParameter(f"Corpus dir not found: {corpus_dir}")
    if not queries_path.exists():
        raise typer.BadParameter(f"Queries file not found: {queries_path}")

    provenance = DatasetProvenance(
        dataset_id=dataset_id,
        source_url=str(dataset_path),
        checksum_sha256=sha256_file(dataset_path),
        license_note="See dataset source/license terms",
        fetched_at_utc="unknown",
    )

    config = RunConfig(
        run_id=resolved_run_id,
        dataset_id=dataset_id,
        dataset_path=str(dataset_path),
        corpus_dir=str(corpus_dir),
        queries_path=str(queries_path),
        output_root=str(output_root),
        providers=provider_list,
        top_k=top_k,
        bm_source=bm_source,
        bm_local_path=bm_local_path,
        allow_provider_skip=allow_provider_skip,
    )

    run_dir = run_retrieval(run_config=config, dataset=provenance)
    console.print(f"Retrieval run complete: [green]{run_dir}[/green]")


@run_app.command("concurrent-write")
def run_concurrent_write_command(
    writers: int = typer.Option(4, "--writers", help="Concurrent MCP client sessions"),
    notes_per_writer: int = typer.Option(25, "--notes-per-writer"),
    edit_ratio: float = typer.Option(
        0.4, "--edit-ratio", help="Per-note probability of hub/own-note append edits"
    ),
    hub_notes: int = typer.Option(4, "--hub-notes", help="Shared contended notes all writers edit"),
    relation_pool: int = typer.Option(
        8, "--relation-pool", help="Shared relation-target pool size"
    ),
    seed: int = typer.Option(42, "--seed"),
    run_id: str | None = typer.Option(None, "--run-id"),
    output_root: Path = typer.Option(Path("benchmarks/runs"), "--output-root"),
    bm_source: str = typer.Option("local-checkout", "--bm-source"),
    bm_local_path: Path = typer.Option(
        ...,
        "--bm-local-path",
        exists=True,
        file_okay=False,
        resolve_path=True,
        help="Pinned Basic Memory git checkout to benchmark",
    ),
    max_seconds: float | None = typer.Option(
        None, "--max-seconds", help="Optional wall-clock cap for the concurrent phase"
    ),
    op_timeout: float = typer.Option(120.0, "--op-timeout"),
    settle_timeout: float = typer.Option(180.0, "--settle-timeout"),
    measure_reindex: bool = typer.Option(True, "--measure-reindex/--no-measure-reindex"),
    strict: bool = typer.Option(
        False,
        "--strict/--no-strict",
        help="Exit nonzero when convergence checks fail; the default records divergence",
    ),
) -> None:
    """Run independent MCP writers against one shared project (basic-memory#1248)."""
    resolved_run_id = run_id or f"cw-{uuid.uuid4().hex[:12]}"
    config = ConcurrentWriteConfig(
        run_id=resolved_run_id,
        writers=writers,
        notes_per_writer=notes_per_writer,
        edit_ratio=edit_ratio,
        hub_notes=hub_notes,
        relation_pool=relation_pool,
        seed=seed,
        output_root=str(output_root),
        bm_source=bm_source,
        bm_local_path=str(bm_local_path),
        max_seconds=max_seconds,
        op_timeout_seconds=op_timeout,
        settle_timeout_seconds=settle_timeout,
        measure_reindex=measure_reindex,
    )
    run_dir = run_concurrent_write(config)
    console.print(f"Concurrent-write run complete: [green]{run_dir}[/green]")

    if strict:
        summary = json.loads(
            (run_dir / "concurrent-write-summary.json").read_text(encoding="utf-8")
        )
        if not summary["converged"]:
            console.print("[red]Convergence checks failed (--strict)[/red]")
            raise typer.Exit(code=1)


@run_app.command("agent-tasks")
def run_agent_tasks_command(
    surfaces: str = typer.Option(
        "rich", "--surfaces", help="Comma-separated tool surfaces: rich,posix (issue #1401)"
    ),
    model: str = typer.Option(
        ...,
        "--model",
        help="Agent under test: openai-compat:<model>@<base_url> | scripted:<path.json>",
    ),
    model_header: list[str] | None = typer.Option(
        None,
        "--model-header",
        help="Extra HTTP header for the agent endpoint as 'Name=value' (repeatable); "
        "e.g. anthropic-workspace-id=wrkspc_... for identity-linked Anthropic keys. "
        "Header names are matched case-insensitively, so 'authorization=...' "
        "replaces the bearer derived from OPENAI_API_KEY rather than joining it. "
        "Values are never recorded in run artifacts.",
    ),
    model_temperature: str = typer.Option(
        "0",
        "--model-temperature",
        help="Sampling temperature for the agent endpoint, or 'omit' to send none "
        "(Claude 5 models reject the parameter). Recorded in the run config.",
    ),
    judge: str | None = typer.Option(
        None,
        "--judge",
        help="Judge runner spec (claude:<model> or openai-compat:...); required only "
        "when a selected task uses a judge_rubric grader",
    ),
    tasks: str = typer.Option(
        "", "--tasks", help="Comma-separated task ids; empty runs all shipped tasks"
    ),
    task_manifest: Path | None = typer.Option(
        None,
        "--task-manifest",
        help="Converted dataset tasks.json (e.g. `convert xafs`); replaces the shipped "
        "task set with grouped, judge-graded questions run on read-only surfaces. "
        "When set and --corpus-dir is left at its default, the corpus dir becomes "
        "<manifest dir>/groups",
    ),
    corpus_dir: Path = typer.Option(Path("benchmarks/datasets/agent-tasks/corpus"), "--corpus-dir"),
    output_root: Path = typer.Option(Path("benchmarks/runs"), "--output-root"),
    run_id: str | None = typer.Option(None, "--run-id"),
    bm_source: str = typer.Option("local-checkout", "--bm-source"),
    bm_local_path: Path = typer.Option(
        ...,
        "--bm-local-path",
        exists=True,
        file_okay=False,
        resolve_path=True,
        help="Pinned Basic Memory git checkout to benchmark",
    ),
    max_turns: int = typer.Option(20, "--max-turns"),
    max_total_tokens: int = typer.Option(200_000, "--max-total-tokens"),
    task_timeout: float = typer.Option(300.0, "--task-timeout"),
    tool_timeout: float = typer.Option(120.0, "--tool-timeout"),
    settle_timeout: float = typer.Option(180.0, "--settle-timeout"),
    allow_surface_skip: bool = typer.Option(
        True,
        "--allow-surface-skip/--strict-surfaces",
        help="Record an unavailable surface as skipped (default) or abort the run",
    ),
    strict: bool = typer.Option(
        False,
        "--strict/--no-strict",
        help="Exit nonzero when any surface is not ok or any task errored; the pass "
        "rate itself is never a gate",
    ),
) -> None:
    """Agent-in-the-loop eval: same tasks/model/budget, tool surface varies (#1401)."""
    # dict.fromkeys dedupes while preserving input order (mirroring select_tasks);
    # a duplicated surface must not create the same surface home twice mid-run.
    surface_list = list(dict.fromkeys(item.strip() for item in surfaces.split(",") if item.strip()))
    if not surface_list:
        raise typer.BadParameter("--surfaces must name at least one surface")
    unknown_surfaces = [name for name in surface_list if name not in SURFACES]
    if unknown_surfaces:
        raise typer.BadParameter(f"Unknown surfaces: {unknown_surfaces}. Known: {sorted(SURFACES)}")

    task_ids = [item.strip() for item in tasks.split(",") if item.strip()]
    # Manifest runs almost always want the converter's sibling groups/ dir; the
    # shipped-corpus default would checksum the wrong corpus and then fail on
    # missing group subtrees, so derive it unless --corpus-dir was set.
    if task_manifest is not None and corpus_dir == Path("benchmarks/datasets/agent-tasks/corpus"):
        corpus_dir = task_manifest.parent / "groups"
    try:
        if task_manifest is not None:
            selected = load_task_manifest(task_manifest, task_ids=task_ids)
        else:
            selected = select_tasks(task_ids)
    except (ValueError, FileNotFoundError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    # Extra endpoint headers stay out of AgentTasksConfig (and therefore out
    # of run artifacts): values may be sensitive, so they ride only in the
    # model factory closure below.
    header_pairs: dict[str, str] = {}
    for raw_header in model_header or []:
        name, separator, value = raw_header.partition("=")
        if not separator or not name.strip() or not value.strip():
            raise typer.BadParameter(f"--model-header must be 'Name=value', got {raw_header!r}")
        header_pairs[name.strip()] = value.strip()

    temperature = parse_model_temperature(model_temperature)

    # Fail fast at parse time: a bad model spec (including claude:) and a
    # missing judge for judge-graded tasks must not survive to mid-run.
    try:
        create_tool_agent_model(model, extra_headers=header_pairs or None, temperature=temperature)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    judged = [
        task.id
        for task in selected
        if any(isinstance(grader, JudgeRubric) for grader in task.graders)
    ]
    if judged and judge is None:
        raise typer.BadParameter(f"Tasks {judged} use judge_rubric graders; pass --judge")

    resolved_run_id = run_id or f"at-{uuid.uuid4().hex[:12]}"
    # AgentTasksConfig owns the field rules (finite temperature, argv/path-safe
    # run_id) so the direct run_agent_tasks(config) path is guarded too. Render
    # its rejection as a CLI parameter error instead of a Pydantic traceback.
    try:
        config = AgentTasksConfig(
            run_id=resolved_run_id,
            surfaces=surface_list,
            task_ids=task_ids,
            task_manifest=str(task_manifest) if task_manifest is not None else None,
            model_spec=model,
            model_temperature=temperature,
            judge_spec=judge,
            corpus_dir=str(corpus_dir),
            output_root=str(output_root),
            bm_source=bm_source,
            bm_local_path=str(bm_local_path),
            budget=AgentBudget(
                max_turns=max_turns,
                max_total_tokens=max_total_tokens,
                max_task_seconds=task_timeout,
            ),
            tool_timeout_seconds=tool_timeout,
            settle_timeout_seconds=settle_timeout,
            allow_surface_skip=allow_surface_skip,
        )
    except ValidationError as exc:
        raise typer.BadParameter(str(exc)) from exc
    run_dir = run_agent_tasks(
        config,
        model_factory=partial(
            create_tool_agent_model,
            extra_headers=header_pairs or None,
            temperature=temperature,
        ),
    )
    console.print(f"Agent-task run complete: [green]{run_dir}[/green]")
    console.print(f"See [cyan]{run_dir / 'summary.md'}[/cyan]")

    if strict:
        statuses = json.loads((run_dir / "surface-status.json").read_text(encoding="utf-8"))
        not_ok = [status for status in statuses if status["state"] != "ok"]
        task_rows = [
            json.loads(line)
            for line in (run_dir / "per-task-agent.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        errored = [row for row in task_rows if row.get("error")]
        if not_ok or errored:
            console.print(
                f"[red]--strict: {len(not_ok)} surface(s) not ok,"
                f" {len(errored)} errored task(s)[/red]"
            )
            raise typer.Exit(code=1)


@run_app.command("qa")
def run_qa_command(
    run_dir: Path = typer.Option(..., "--run-dir"),
    answerer: str = typer.Option(
        "claude:claude-haiku-4-5",
        "--answerer",
        help="Runner spec: claude:<model> or openai-compat:<model>@<base_url>",
    ),
    judge: str = typer.Option(
        "claude:claude-sonnet-4-6",
        "--judge",
        help="Runner spec: claude:<model> or openai-compat:<model>@<base_url>",
    ),
    max_workers: int = typer.Option(4, "--max-workers"),
    max_context_chars: int | None = typer.Option(
        None,
        "--max-context-chars",
        help="Override the assembled-context budget (default 12000). Use a large value for full-context baselines.",
    ),
) -> None:
    out = run_qa_stage(
        run_dir=run_dir,
        answerer_spec=answerer,
        judge_spec=judge,
        max_workers=max_workers,
        max_context_chars=max_context_chars,
    )
    console.print(f"QA run complete: [green]{out}[/green]")
    console.print(f"See [cyan]{out / 'qa-summary.json'}[/cyan]")


@run_app.command("review")
def run_review_command(
    run_dir: Path = typer.Option(..., "--run-dir"),
    source: str = typer.Option("auto", "--source", help="qa | rejudge | auto"),
) -> None:
    """Render a self-contained judge-review/labeling HTML report for a run."""
    out = run_review_stage(run_dir=run_dir, source=source)
    console.print(f"Review report: [green]{out}[/green]")
    console.print(f"Open it: [cyan]open {out}[/cyan]")


@run_app.command("diagnose")
def run_diagnose_command(
    run_dir: Path = typer.Option(..., "--run-dir"),
    source: str = typer.Option("auto", "--source", help="qa | rejudge | auto"),
    recall_field: str = typer.Option(
        "recall_at_10", "--recall-field", help="recall_at_5 | recall_at_10"
    ),
) -> None:
    """Attribute QA failures to retrieval vs the answerer (per provider).

    Separates "retrieved but unused" (the fixed answerer's fault, identical
    across providers) from "truly missed" (a real retrieval failure), so QA
    accuracy can be read honestly against the retrieval ceiling.
    """
    from rich.table import Table

    out = run_diagnose_stage(run_dir=run_dir, source=source, recall_field=recall_field)
    payload = json.loads(out.read_text(encoding="utf-8"))

    table = Table(title=f"Failure attribution — {run_dir.name} ({payload['source']})")
    table.add_column("provider")
    table.add_column("answerable", justify="right")
    table.add_column("QA acc", justify="right")
    table.add_column("retr. ceiling", justify="right")
    table.add_column("answerer gap", justify="right")
    table.add_column("retrieval gap", justify="right")
    table.add_column("of fails: answerer", justify="right")
    for prov in payload["providers"]:
        table.add_row(
            prov["provider"],
            str(prov["answerable"]),
            f"{prov['qa_accuracy']:.3f}",
            f"{prov['retrieval_ceiling']:.3f}",
            f"{prov['answerer_gap']:.3f}",
            f"{prov['retrieval_gap']:.3f}",
            f"{prov['answerer_failure_share']:.0%}",
        )
    console.print(table)
    console.print(f"Wrote [green]{out}[/green]")


@run_app.command("beam-score")
def run_beam_score_command(
    run_dir: Path = typer.Option(..., "--run-dir"),
    judge: str = typer.Option(
        "claude:claude-sonnet-4-6",
        "--judge",
        help="Runner spec: claude:<model> or openai-compat:<model>@<base_url>",
    ),
    source: str = typer.Option("auto", "--source", help="qa | rejudge | auto"),
    max_workers: int = typer.Option(4, "--max-workers"),
) -> None:
    """Score a BEAM run's stored QA answers with the nugget methodology.

    Requires a completed `run qa` on a corpus converted via `convert beam`.
    Writes per-query-beam.jsonl, beam-summary.json, and beam-summary.md with
    per-ability scores (never just an overall average).
    """
    out = run_beam_score_stage(
        run_dir=run_dir, judge_spec=judge, source=source, max_workers=max_workers
    )
    console.print(f"BEAM scoring complete: [green]{out}[/green]")
    console.print(f"See [cyan]{out / 'beam-summary.md'}[/cyan]")


@run_app.command("rejudge")
def run_rejudge_command(
    run_dir: Path = typer.Option(..., "--run-dir"),
    judge: str = typer.Option("claude:claude-sonnet-4-6", "--judge"),
    max_workers: int = typer.Option(4, "--max-workers"),
) -> None:
    """Re-judge stored QA answers (no regeneration); reports verdict flips."""
    out = run_rejudge_stage(run_dir=run_dir, judge_spec=judge, max_workers=max_workers)
    console.print(f"Re-judge complete: [green]{out}[/green]")
    console.print(f"Flips: [cyan]{out / 'qa-rejudge-flips.json'}[/cyan]")


@run_app.command("full")
def run_full_command(
    providers: str = typer.Option("bm-local,mem0-local", "--providers"),
    dataset_id: str = typer.Option("locomo", "--dataset-id"),
    dataset_path: Path = typer.Option(
        Path("benchmarks/datasets/locomo/locomo10.json"), "--dataset-path"
    ),
    corpus_dir: Path = typer.Option(Path("benchmarks/generated/locomo/docs"), "--corpus-dir"),
    queries_path: Path = typer.Option(
        Path("benchmarks/generated/locomo/queries.json"), "--queries-path"
    ),
    output_root: Path = typer.Option(Path("benchmarks/runs"), "--output-root"),
    run_id: str | None = typer.Option(None, "--run-id"),
    top_k: int = typer.Option(10, "--top-k"),
    bm_source: str = typer.Option("github:basicmachines-co/basic-memory@main", "--bm-source"),
    bm_local_path: str | None = typer.Option(None, "--bm-local-path"),
    allow_provider_skip: bool = typer.Option(True, "--allow-provider-skip/--strict-providers"),
) -> None:
    run_retrieval_command(
        providers=providers,
        dataset_id=dataset_id,
        dataset_path=dataset_path,
        corpus_dir=corpus_dir,
        queries_path=queries_path,
        output_root=output_root,
        run_id=run_id,
        top_k=top_k,
        bm_source=bm_source,
        bm_local_path=bm_local_path,
        allow_provider_skip=allow_provider_skip,
    )


@app.command("compare")
def compare_runs(
    baseline: Path = typer.Argument(..., help="Path to baseline retrieval-summary.json"),
    candidate: Path = typer.Argument(..., help="Path to candidate retrieval-summary.json"),
    provider: str = typer.Option("bm-local", "--provider"),
    metric: str = typer.Option("recall_at_5", "--metric"),
) -> None:
    baseline_payload = load_retrieval_summary(baseline)
    candidate_payload = load_retrieval_summary(candidate)
    b, c, delta = compare_provider_metric(baseline_payload, candidate_payload, provider, metric)
    console.print(f"provider={provider} metric={metric}")
    console.print(f"baseline={b}")
    console.print(f"candidate={c}")
    console.print(f"delta={delta}")


@app.command("publish")
def publish_run(
    run_dir: Path = typer.Option(..., "--run-dir"),
    destination: Path = typer.Option(Path("benchmarks/results/public"), "--destination"),
) -> None:
    if not run_dir.exists():
        raise typer.BadParameter(f"Run directory does not exist: {run_dir}")
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / run_dir.name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(run_dir, target)
    console.print(f"Published run to [green]{target}[/green]")


@app.command("validate-artifacts")
def validate_artifacts(
    run_dir: Path = typer.Option(..., "--run-dir"),
) -> None:
    expected = [
        "manifest.json",
        "provider-status.json",
        "per-query-retrieval.jsonl",
        "retrieval-summary.json",
        "summary.md",
    ]
    missing = [name for name in expected if not (run_dir / name).exists()]
    if missing:
        raise typer.BadParameter(f"Missing artifacts: {missing}")
    console.print("Artifacts look complete.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
