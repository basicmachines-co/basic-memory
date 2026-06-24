"""CLI entrypoint for benchmark operations."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import typer
from rich.console import Console

from basic_memory_benchmarks.converters.locomo_to_corpus import convert_locomo_to_corpus
from basic_memory_benchmarks.converters.longmemeval_to_corpus import convert_longmemeval_to_corpus
from basic_memory_benchmarks.datasets.locomo import LOCOMO_URL, fetch_locomo_dataset
from basic_memory_benchmarks.converters.convomem_to_corpus import convert_convomem_to_corpus
from basic_memory_benchmarks.datasets.convomem import fetch_convomem_batches
from basic_memory_benchmarks.datasets.locomo_audit import fetch_locomo_audit_corrections
from basic_memory_benchmarks.datasets.longmemeval import (
    LONGMEMEVAL_S_URL,
    fetch_longmemeval_dataset,
)
from basic_memory_benchmarks.models import DatasetProvenance, RunConfig
from basic_memory_benchmarks.reporting.compare import (
    compare_provider_metric,
    load_retrieval_summary,
)
from basic_memory_benchmarks.runner import (
    run_diagnose_stage,
    run_judge,
    run_qa_stage,
    run_rejudge_stage,
    run_review_stage,
    run_retrieval,
)
from basic_memory_benchmarks.utils import sha256_file

app = typer.Typer(help="Basic Memory benchmark suite")
console = Console()

datasets_app = typer.Typer(help="Dataset management commands")
convert_app = typer.Typer(help="Dataset conversion commands")
run_app = typer.Typer(help="Benchmark execution commands")

app.add_typer(datasets_app, name="datasets")
app.add_typer(convert_app, name="convert")
app.add_typer(run_app, name="run")


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

    console.print(f"Structured ([green]{mode}[/green]): [cyan]{output_dir}[/cyan] ({doc_count} docs)")
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
    import json

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


@run_app.command("judge")
def run_judge_command(
    run_dir: Path = typer.Option(..., "--run-dir"),
    model: str = typer.Option("gpt-4o-mini", "--model"),
) -> None:
    out = run_judge(run_dir=run_dir, model=model)
    console.print(f"Judge run complete: [green]{out}[/green]")


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
    judge: bool = typer.Option(False, "--judge"),
    judge_model: str = typer.Option("gpt-4o-mini", "--judge-model"),
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

    if judge:
        resolved_run_id = run_id
        if resolved_run_id is None:
            # run_retrieval_command generated uuid when run_id is None. infer by latest dir.
            run_dirs = sorted(Path(output_root).glob("*"), key=lambda path: path.stat().st_mtime)
            if not run_dirs:
                raise RuntimeError("Unable to locate run directory for judge step")
            run_dir = run_dirs[-1]
        else:
            run_dir = Path(output_root) / resolved_run_id
        run_judge_command(run_dir=run_dir, model=judge_model)


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
