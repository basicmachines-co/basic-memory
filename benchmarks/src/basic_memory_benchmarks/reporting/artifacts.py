"""Artifact writing helpers."""

from __future__ import annotations

import json
from pathlib import Path

from basic_memory_benchmarks.models import (
    PerQueryRetrievalResult,
    ProviderStatus,
    RetrievalSummary,
    RunManifest,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, sort_keys=True) + "\n")


def write_artifacts(
    *,
    run_dir: Path,
    manifest: RunManifest,
    provider_status: list[ProviderStatus],
    retrieval_rows: list[PerQueryRetrievalResult],
    retrieval_summaries: list[RetrievalSummary],
    fairness_warnings: list[str],
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(run_dir / "manifest.json", manifest.model_dump(mode="json"))
    _write_json(
        run_dir / "provider-status.json",
        [row.model_dump(mode="json") for row in provider_status],
    )
    _write_jsonl(
        run_dir / "per-query-retrieval.jsonl",
        [row.model_dump(mode="json") for row in retrieval_rows],
    )
    _write_json(
        run_dir / "retrieval-summary.json",
        {
            "providers": [row.model_dump(mode="json") for row in retrieval_summaries],
            "fairness_warnings": fairness_warnings,
        },
    )

    summary_markdown = build_summary_markdown(
        manifest=manifest,
        provider_status=provider_status,
        retrieval_summaries=retrieval_summaries,
        fairness_warnings=fairness_warnings,
    )
    (run_dir / "summary.md").write_text(summary_markdown, encoding="utf-8")


def build_summary_markdown(
    *,
    manifest: RunManifest,
    provider_status: list[ProviderStatus],
    retrieval_summaries: list[RetrievalSummary],
    fairness_warnings: list[str],
) -> str:
    lines: list[str] = []
    lines.append(f"# Benchmark Run `{manifest.run_id}`")
    lines.append("")
    lines.append("## Provenance")
    lines.append("")
    lines.append(f"- Benchmark SHA: `{manifest.benchmark_git_sha}`")
    lines.append(f"- BM source: `{manifest.bm_source}`")
    lines.append(f"- BM resolved SHA: `{manifest.bm_resolved_sha or 'unknown'}`")
    lines.append(f"- Dataset: `{manifest.dataset.dataset_id}`")
    lines.append(f"- Dataset source: {manifest.dataset.source_url}")
    lines.append(f"- Dataset checksum: `{manifest.dataset.checksum_sha256}`")
    lines.append("")

    lines.append("## Provider Status")
    lines.append("")
    lines.append("| Provider | State | Reason |")
    lines.append("| --- | --- | --- |")
    for item in provider_status:
        lines.append(f"| {item.provider} | {item.state} | {item.reason or ''} |")
    lines.append("")

    lines.append("## Retrieval Summary")
    lines.append("")
    lines.append(
        "| Provider | Recall@5 | Recall@10 | MRR | Precision@5 | Content Hit | Mean ms | P95 ms |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for summary in retrieval_summaries:
        metric = summary.metrics
        lines.append(
            "| "
            f"{summary.provider} | {metric.recall_at_5:.3f} | {metric.recall_at_10:.3f} | "
            f"{metric.mrr:.3f} | {metric.precision_at_5:.3f} | {metric.content_hit_rate:.3f} | "
            f"{metric.mean_latency_ms:.1f} | {metric.p95_latency_ms:.1f} |"
        )
    lines.append("")

    # The headline/breakout containers are generic; the section labels follow
    # the dataset (BEAM: answerable abilities vs abstention; default: the
    # LoCoMo category split).
    if manifest.config.dataset_id.startswith("beam"):
        headline_label = "## Answerable Abilities (BEAM headline)"
        breakout_label = "## Abstention Breakout"
    else:
        headline_label = "## Official Headline (LoCoMo Categories 1-4)"
        breakout_label = "## Adversarial (Category 5)"

    lines.append(headline_label)
    lines.append("")
    lines.append("| Provider | Recall@5 | Recall@10 | MRR |")
    lines.append("| --- | --- | --- | --- |")
    for summary in retrieval_summaries:
        metric = summary.official_headline
        lines.append(
            f"| {summary.provider} | {metric.recall_at_5:.3f} | {metric.recall_at_10:.3f} | {metric.mrr:.3f} |"
        )
    lines.append("")

    lines.append(breakout_label)
    lines.append("")
    lines.append("| Provider | Recall@5 | Recall@10 | MRR |")
    lines.append("| --- | --- | --- | --- |")
    for summary in retrieval_summaries:
        metric = summary.adversarial_breakout
        lines.append(
            f"| {summary.provider} | {metric.recall_at_5:.3f} | {metric.recall_at_10:.3f} | {metric.mrr:.3f} |"
        )
    lines.append("")

    if fairness_warnings:
        lines.append("## Fairness Warnings")
        lines.append("")
        for warning in fairness_warnings:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append("## Reproduce")
    lines.append("")
    lines.append("```bash")
    lines.append(
        f"uv run bm-bench run retrieval --run-id {manifest.run_id} --dataset-id {manifest.config.dataset_id}"
    )
    lines.append("```")

    return "\n".join(lines).strip() + "\n"
