"""Benchmark runner orchestration."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from basic_memory_benchmarks.exceptions import ProviderSkippedError
from basic_memory_benchmarks.fairness import validate_fairness
from basic_memory_benchmarks.models import (
    DatasetProvenance,
    PerQueryRetrievalResult,
    ProviderStatus,
    QueryCase,
    RetrievalSummary,
    RunConfig,
    RunManifest,
    RuntimeInfo,
)
from basic_memory_benchmarks.providers import create_provider
from basic_memory_benchmarks.providers.base import BenchmarkProvider
from basic_memory_benchmarks.reporting.artifacts import write_artifacts
from basic_memory_benchmarks.scoring.judge import run_optional_judge
from basic_memory_benchmarks.scoring.retrieval import evaluate_query, summarize_provider
from basic_memory_benchmarks.utils import (
    git_sha,
    resolve_remote_main_sha,
    runtime_info,
    utc_now_iso,
)


def load_queries(path: Path) -> list[QueryCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Query file must contain a list: {path}")
    return [QueryCase.model_validate(item) for item in payload]


def _resolve_bm_sha(run_config: RunConfig) -> str | None:
    if run_config.bm_local_path:
        local_sha = git_sha(Path(run_config.bm_local_path))
        if local_sha:
            return local_sha
    return resolve_remote_main_sha("https://github.com/basicmachines-co/basic-memory")


def _execute_provider_flat(
    *,
    provider: BenchmarkProvider,
    provider_name: str,
    queries: list[QueryCase],
    corpus_path: Path,
    run_config: RunConfig,
    cleanup_after: bool = True,
) -> list[PerQueryRetrievalResult]:
    """Classic single-corpus execution: one ingest, then every query.

    ``cleanup_after=False`` is the group-reuse path: the grouped executor owns
    the provider's lifecycle and cleans up once at end of run.
    """
    provider_rows: list[PerQueryRetrievalResult] = []
    try:
        provider.ingest(corpus_path, run_config)
        for query in queries:
            started = time.perf_counter()
            hits = provider.search(query.query, run_config.top_k, run_config)
            latency_ms = (time.perf_counter() - started) * 1000.0
            provider_rows.append(
                evaluate_query(
                    provider=provider_name,
                    query=query,
                    hits=hits,
                    latency_ms=latency_ms,
                )
            )
    finally:
        if cleanup_after:
            try:
                provider.cleanup(run_config)
            except Exception:
                # Cleanup errors should not mask run state.
                pass
    return provider_rows


def _execute_provider_grouped(
    *,
    provider_factory: Callable[[str], BenchmarkProvider],
    provider_name: str,
    queries: list[QueryCase],
    corpus_path: Path,
    run_config: RunConfig,
) -> tuple[list[PerQueryRetrievalResult], BenchmarkProvider, dict[str, str]]:
    """Grouped execution (LongMemEval): each group is its own isolated corpus.

    Per group, a fresh provider instance ingests ``<corpus>/<group>/docs``
    under a group-suffixed run id, so provider-side namespaces (BM project
    name, mem0 user id) never leak content across groups. A failing group is
    recorded and skipped rather than aborting the run; ProviderSkippedError on
    the first group means the provider is unavailable and propagates.
    """
    groups: dict[str, list[QueryCase]] = {}
    for query in queries:
        if query.group is None:
            raise ValueError(
                f"Query {query.id} has no group but the query set is grouped; "
                "mixed grouped/ungrouped query files are not supported"
            )
        groups.setdefault(query.group, []).append(query)

    provider_rows: list[PerQueryRetrievalResult] = []
    failed_groups: list[str] = []
    failed_group_errors: dict[str, str] = {}
    last_provider: BenchmarkProvider | None = None
    shared_provider = provider_factory(provider_name)
    reuse = shared_provider.supports_group_reuse
    try:
        for group_index, (group_id, group_queries) in enumerate(sorted(groups.items())):
            group_corpus = corpus_path / group_id / "docs"
            if not group_corpus.exists():
                raise FileNotFoundError(f"Missing group corpus: {group_corpus}")
            group_config = run_config.model_copy(
                update={"run_id": f"{run_config.run_id}-{group_id}"}
            )
            # Non-reuse providers still use the shared instance for the first
            # group so capability probing doesn't cost an extra instance.
            if reuse or group_index == 0:
                provider = shared_provider
            else:
                provider = provider_factory(provider_name)
            try:
                provider_rows.extend(
                    _execute_provider_flat(
                        provider=provider,
                        provider_name=provider_name,
                        queries=group_queries,
                        corpus_path=group_corpus,
                        run_config=group_config,
                        cleanup_after=not reuse,
                    )
                )
                last_provider = provider
            except ProviderSkippedError:
                # Trigger: provider signals it cannot run at all (missing creds).
                # Why: the first group is representative; retrying hundreds of
                # groups against an unavailable provider wastes hours.
                # Outcome: the provider is recorded as skipped for the whole run.
                if group_index == 0:
                    raise
                failed_groups.append(group_id)
            except Exception as exc:
                failed_groups.append(group_id)
                # Keep the first few error messages: a silent failed-group
                # list is undiagnosable after a multi-hour run.
                if len(failed_group_errors) < 3:
                    failed_group_errors[group_id] = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        if reuse:
            try:
                shared_provider.cleanup(run_config)
            except Exception:
                # Cleanup errors should not mask run state.
                pass

    if last_provider is None:
        # Surface the first captured errors: an opaque "all failed" with no
        # cause is undiagnosable after a multi-hour run.
        detail = "; ".join(f"{gid}: {msg}" for gid, msg in sorted(failed_group_errors.items()))
        raise RuntimeError(
            f"All {len(failed_groups)} groups failed for provider {provider_name}"
            + (f" — {detail}" if detail else "")
        )

    group_metadata: dict[str, str] = {
        "grouped_mode": "true",
        "group_count": str(len(groups)),
    }
    if failed_groups:
        group_metadata["failed_group_count"] = str(len(failed_groups))
        group_metadata["failed_groups"] = ",".join(sorted(failed_groups)[:50])
        for index, (group_id, message) in enumerate(sorted(failed_group_errors.items())):
            group_metadata[f"failed_group_error_{index}"] = f"{group_id}: {message}"
    return provider_rows, last_provider, group_metadata


def run_retrieval(
    *,
    run_config: RunConfig,
    dataset: DatasetProvenance,
    provider_factory: Callable[[str], BenchmarkProvider] = create_provider,
) -> Path:
    queries = load_queries(Path(run_config.queries_path))
    corpus_path = Path(run_config.corpus_dir)
    output_root = Path(run_config.output_root)
    run_dir = output_root / run_config.run_id
    grouped = any(query.group is not None for query in queries)

    retrieval_rows: list[PerQueryRetrievalResult] = []
    provider_status: list[ProviderStatus] = []
    summaries: list[RetrievalSummary] = []

    rows_by_provider: dict[str, list[PerQueryRetrievalResult]] = {}

    for provider_name in run_config.providers:
        try:
            group_metadata: dict[str, str] = {}
            if grouped:
                provider_rows, version_provider, group_metadata = _execute_provider_grouped(
                    provider_factory=provider_factory,
                    provider_name=provider_name,
                    queries=queries,
                    corpus_path=corpus_path,
                    run_config=run_config,
                )
            else:
                version_provider = provider_factory(provider_name)
                provider_rows = _execute_provider_flat(
                    provider=version_provider,
                    provider_name=provider_name,
                    queries=queries,
                    corpus_path=corpus_path,
                    run_config=run_config,
                )

            summary = summarize_provider(provider_name, provider_rows)
            summaries.append(summary)
            retrieval_rows.extend(provider_rows)
            rows_by_provider[provider_name] = provider_rows
            provider_status.append(
                ProviderStatus(
                    provider=provider_name,
                    state="ok",
                    metadata={**version_provider.version_info(), **group_metadata},
                )
            )
        except ProviderSkippedError as exc:
            provider_status.append(
                ProviderStatus(provider=provider_name, state="skipped", reason=str(exc))
            )
            if not run_config.allow_provider_skip:
                raise
        except Exception as exc:
            provider_status.append(
                ProviderStatus(provider=provider_name, state="error", reason=str(exc))
            )
            if not run_config.allow_provider_skip:
                raise

    fairness_warnings = validate_fairness(rows_by_provider)

    os_name, py_version = runtime_info()
    manifest = RunManifest(
        run_id=run_config.run_id,
        created_at_utc=utc_now_iso(),
        benchmark_git_sha=git_sha(Path.cwd()) or "unknown",
        bm_source=run_config.bm_source,
        bm_resolved_sha=_resolve_bm_sha(run_config),
        bm_local_path=run_config.bm_local_path,
        mem0_version=next(
            (
                status.metadata.get("mem0ai")
                for status in provider_status
                if status.provider == "mem0-local" and status.metadata
            ),
            None,
        ),
        provider_versions={
            status.provider: status.metadata for status in provider_status if status.metadata
        },
        dataset=dataset,
        runtime=RuntimeInfo(os=os_name, python_version=py_version, started_at_utc=utc_now_iso()),
        config=run_config,
    )

    write_artifacts(
        run_dir=run_dir,
        manifest=manifest,
        provider_status=provider_status,
        retrieval_rows=retrieval_rows,
        retrieval_summaries=summaries,
        fairness_warnings=fairness_warnings,
    )
    return run_dir


def _load_retrieval_rows(run_dir: Path) -> list[PerQueryRetrievalResult]:
    retrieval_path = run_dir / "per-query-retrieval.jsonl"
    if not retrieval_path.exists():
        raise FileNotFoundError(f"Missing retrieval artifact: {retrieval_path}")
    rows: list[PerQueryRetrievalResult] = []
    with retrieval_path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                rows.append(PerQueryRetrievalResult.model_validate(json.loads(line)))
    return rows


def run_qa_stage(
    *,
    run_dir: Path,
    answerer_spec: str,
    judge_spec: str,
    max_workers: int = 4,
    max_context_chars: int | None = None,
) -> Path:
    """Generate answers from each provider's retrieved context and judge them.

    Reads per-query-retrieval.jsonl, writes per-query-qa.jsonl and
    qa-summary.json into the same run directory.
    """
    from basic_memory_benchmarks.llm.runners import create_runner
    from basic_memory_benchmarks.scoring.qa import CONTEXT_MAX_CHARS, run_qa

    answerer = create_runner(answerer_spec)
    judge = create_runner(judge_spec)

    grouped: dict[str, list[PerQueryRetrievalResult]] = {}
    for row in _load_retrieval_rows(run_dir):
        grouped.setdefault(row.provider, []).append(row)

    qa_rows = []
    qa_summaries = []
    for provider, provider_rows in grouped.items():
        provider_cases, provider_summary = run_qa(
            provider_rows,
            provider=provider,
            answerer=answerer,
            judge=judge,
            max_workers=max_workers,
            max_context_chars=max_context_chars or CONTEXT_MAX_CHARS,
        )
        qa_rows.extend(provider_cases)
        qa_summaries.append(provider_summary)

    qa_jsonl = run_dir / "per-query-qa.jsonl"
    with qa_jsonl.open("w", encoding="utf-8") as file:
        for row in qa_rows:
            file.write(json.dumps(row.model_dump(mode="json"), sort_keys=True) + "\n")

    qa_summary_path = run_dir / "qa-summary.json"
    qa_summary_path.write_text(
        json.dumps(
            {"providers": [item.model_dump(mode="json") for item in qa_summaries]},
            indent=2,
        ),
        encoding="utf-8",
    )
    return run_dir


def run_review_stage(
    *,
    run_dir: Path,
    source: str = "auto",
) -> Path:
    """Render a self-contained judge-review HTML report for a run.

    ``source``: 'qa' uses per-query-qa.jsonl, 'rejudge' uses
    per-query-qa-rejudge.jsonl, 'auto' prefers the re-judged file when present.
    Writes review.html into the run dir and returns its path.
    """
    from basic_memory_benchmarks.scoring.review import build_review_html

    cases, _chosen = _load_qa_cases(run_dir, source)

    review_path = run_dir / "review.html"
    review_path.write_text(build_review_html(cases, run_id=run_dir.name), encoding="utf-8")
    return review_path


def _load_qa_cases(run_dir: Path, source: str):
    """Load QA cases, preferring the re-judged artifact under 'auto'."""
    from basic_memory_benchmarks.models import QACaseResult

    rejudge_path = run_dir / "per-query-qa-rejudge.jsonl"
    qa_path = run_dir / "per-query-qa.jsonl"
    if source == "rejudge" or (source == "auto" and rejudge_path.exists()):
        chosen = rejudge_path
    else:
        chosen = qa_path
    if not chosen.exists():
        raise FileNotFoundError(f"No QA artifact to load: {chosen}")

    cases = []
    with chosen.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                cases.append(QACaseResult.model_validate(json.loads(line)))
    return cases, chosen


def run_diagnose_stage(
    *,
    run_dir: Path,
    source: str = "auto",
    recall_field: str = "recall_at_10",
) -> Path:
    """Attribute QA failures to retrieval vs the answerer for each provider.

    Joins QA verdicts (per-query-qa[-rejudge].jsonl) with retrieval rows
    (per-query-retrieval.jsonl) and writes qa-diagnosis.json, separating
    "retrieved but unused" (answerer) failures from "truly missed" (retrieval)
    failures. ``source``: 'qa' | 'rejudge' | 'auto' (prefers re-judged).
    Returns the path to qa-diagnosis.json.
    """
    from basic_memory_benchmarks.scoring.diagnose import diagnose_run

    qa_cases, chosen = _load_qa_cases(run_dir, source)
    retrieval_rows = _load_retrieval_rows(run_dir)
    diagnoses = diagnose_run(qa_cases, retrieval_rows, recall_field=recall_field)

    out_path = run_dir / "qa-diagnosis.json"
    out_path.write_text(
        json.dumps(
            {
                "source": chosen.name,
                "recall_field": recall_field,
                "providers": [d.model_dump(mode="json") for d in diagnoses],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return out_path


def run_rejudge_stage(
    *,
    run_dir: Path,
    judge_spec: str,
    max_workers: int = 4,
) -> Path:
    """Re-judge stored QA answers with a (possibly different) judge.

    Reads per-query-qa.jsonl, re-runs only the judge on each stored generated
    answer, and writes per-query-qa-rejudge.jsonl, qa-rejudge-summary.json (per
    provider), and qa-rejudge-flips.json (cases whose verdict changed) for
    judge-calibration review. The original QA artifacts are left untouched.
    """
    from basic_memory_benchmarks.llm.runners import create_runner
    from basic_memory_benchmarks.models import QACaseResult
    from basic_memory_benchmarks.scoring.qa import rejudge_cases

    qa_path = run_dir / "per-query-qa.jsonl"
    if not qa_path.exists():
        raise FileNotFoundError(f"Missing QA artifact: {qa_path}")

    cases_by_provider: dict[str, list[QACaseResult]] = {}
    with qa_path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                case = QACaseResult.model_validate(json.loads(line))
                cases_by_provider.setdefault(case.provider, []).append(case)

    judge = create_runner(judge_spec)
    all_rejudged = []
    summaries = []
    all_flips = []
    for provider, cases in cases_by_provider.items():
        rejudged, summary, flips = rejudge_cases(cases, judge=judge, max_workers=max_workers)
        all_rejudged.extend(rejudged)
        summaries.append(summary)
        all_flips.extend(flips)

    rejudge_jsonl = run_dir / "per-query-qa-rejudge.jsonl"
    with rejudge_jsonl.open("w", encoding="utf-8") as file:
        for case in all_rejudged:
            file.write(json.dumps(case.model_dump(mode="json"), sort_keys=True) + "\n")

    (run_dir / "qa-rejudge-summary.json").write_text(
        json.dumps(
            {"judge": judge_spec, "providers": [s.model_dump(mode="json") for s in summaries]},
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "qa-rejudge-flips.json").write_text(
        json.dumps(
            {"judge": judge_spec, "flip_count": len(all_flips), "flips": all_flips}, indent=2
        ),
        encoding="utf-8",
    )
    return run_dir


def run_judge(
    *,
    run_dir: Path,
    model: str,
) -> Path:
    retrieval_path = run_dir / "per-query-retrieval.jsonl"
    if not retrieval_path.exists():
        raise FileNotFoundError(f"Missing retrieval artifact: {retrieval_path}")

    rows: list[PerQueryRetrievalResult] = []
    with retrieval_path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            rows.append(PerQueryRetrievalResult.model_validate(json.loads(line)))

    grouped: dict[str, list[PerQueryRetrievalResult]] = {}
    for row in rows:
        grouped.setdefault(row.provider, []).append(row)

    judge_rows = []
    judge_summaries = []
    for provider, provider_rows in grouped.items():
        provider_case_results, provider_summary = run_optional_judge(
            provider_rows,
            provider=provider,
            model=model,
        )
        judge_rows.extend(provider_case_results)
        judge_summaries.append(provider_summary)

    judge_jsonl = run_dir / "per-query-judge.jsonl"
    with judge_jsonl.open("w", encoding="utf-8") as file:
        for row in judge_rows:
            file.write(json.dumps(row.model_dump(mode="json"), sort_keys=True) + "\n")

    judge_summary_path = run_dir / "judge-summary.json"
    judge_summary_path.write_text(
        json.dumps(
            {"providers": [item.model_dump(mode="json") for item in judge_summaries]}, indent=2
        ),
        encoding="utf-8",
    )
    return run_dir
