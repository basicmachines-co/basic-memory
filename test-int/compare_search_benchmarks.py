#!/usr/bin/env python3
"""Compare two search benchmark JSONL files and report metric deltas."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


LOWER_IS_BETTER_SUFFIXES = ("_ms", "_seconds", "_size_mb", "_size_bytes")
HIGHER_IS_BETTER_SUFFIXES = ("_per_sec",)
HIGHER_IS_BETTER_PREFIXES = ("hit_rate_", "recall_", "mrr_")
EQUAL_IS_BETTER_KEYS = {"notes_indexed", "queries_executed"}


@dataclass(frozen=True)
class BenchmarkRecord:
    benchmark: str
    metrics: dict[str, float]
    timestamp_utc: str | None


def _preference_for_metric(metric_name: str) -> str:
    """Return optimization preference for a metric."""
    if metric_name in EQUAL_IS_BETTER_KEYS:
        return "equal"
    if metric_name.startswith(HIGHER_IS_BETTER_PREFIXES):
        return "higher"
    if metric_name.endswith(HIGHER_IS_BETTER_SUFFIXES):
        return "higher"
    if metric_name.endswith(LOWER_IS_BETTER_SUFFIXES):
        return "lower"
    return "none"


def _classify_delta(metric_name: str, baseline: float, candidate: float) -> str:
    """Classify candidate metric movement relative to baseline."""
    if candidate == baseline:
        return "same"

    preference = _preference_for_metric(metric_name)
    if preference == "higher":
        return "better" if candidate > baseline else "worse"
    if preference == "lower":
        return "better" if candidate < baseline else "worse"
    if preference == "equal":
        return "better" if candidate == baseline else "worse"
    return "n/a"


def _format_delta_percent(baseline: float, delta: float) -> str:
    if baseline == 0:
        return "n/a"
    return f"{(delta / baseline) * 100:+.2f}%"


def _read_latest_records(path: Path) -> dict[str, BenchmarkRecord]:
    records: dict[str, BenchmarkRecord] = {}
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped:
                continue

            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:  # pragma: no cover - invalid file input path
                raise ValueError(f"{path}:{line_number}: invalid JSON ({exc})") from exc

            benchmark = payload.get("benchmark")
            metrics = payload.get("metrics")
            timestamp_utc = payload.get("timestamp_utc")

            if not isinstance(benchmark, str):
                raise ValueError(f"{path}:{line_number}: missing or invalid 'benchmark'")
            if not isinstance(metrics, dict):
                raise ValueError(f"{path}:{line_number}: missing or invalid 'metrics'")
            if timestamp_utc is not None and not isinstance(timestamp_utc, str):
                raise ValueError(f"{path}:{line_number}: invalid 'timestamp_utc'")

            numeric_metrics: dict[str, float] = {}
            for metric_name, metric_value in metrics.items():
                if isinstance(metric_value, bool):
                    continue
                if isinstance(metric_value, (int, float)):
                    numeric_metrics[str(metric_name)] = float(metric_value)

            records[benchmark] = BenchmarkRecord(
                benchmark=benchmark,
                metrics=numeric_metrics,
                timestamp_utc=timestamp_utc,
            )
    return records


def _iter_rows(
    baseline_records: dict[str, BenchmarkRecord],
    candidate_records: dict[str, BenchmarkRecord],
    include_benchmarks: set[str] | None = None,
) -> Iterable[list[str]]:
    common_benchmarks = sorted(set(baseline_records).intersection(candidate_records))
    if include_benchmarks:
        common_benchmarks = [name for name in common_benchmarks if name in include_benchmarks]

    for benchmark in common_benchmarks:
        baseline = baseline_records[benchmark]
        candidate = candidate_records[benchmark]
        common_metrics = sorted(set(baseline.metrics).intersection(candidate.metrics))
        for metric in common_metrics:
            baseline_value = baseline.metrics[metric]
            candidate_value = candidate.metrics[metric]
            delta = candidate_value - baseline_value
            yield [
                benchmark,
                metric,
                f"{baseline_value:.6f}",
                f"{candidate_value:.6f}",
                f"{delta:+.6f}",
                _format_delta_percent(baseline_value, delta),
                _classify_delta(metric, baseline_value, candidate_value),
            ]


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    all_rows = [headers, *rows]
    widths = [max(len(row[index]) for row in all_rows) for index in range(len(headers))]

    def format_row(row: list[str]) -> str:
        return " | ".join(value.ljust(widths[index]) for index, value in enumerate(row))

    print(format_row(headers))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(format_row(row))


def _print_markdown_table(headers: list[str], rows: list[list[str]]) -> None:
    print("| " + " | ".join(headers) + " |")
    print("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        print("| " + " | ".join(row) + " |")


def _print_missing(
    baseline_records: dict[str, BenchmarkRecord],
    candidate_records: dict[str, BenchmarkRecord],
) -> None:
    baseline_only = sorted(set(baseline_records) - set(candidate_records))
    candidate_only = sorted(set(candidate_records) - set(baseline_records))

    if baseline_only:
        print("\nBenchmarks only in baseline:")
        for benchmark in baseline_only:
            print(f"- {benchmark}")

    if candidate_only:
        print("\nBenchmarks only in candidate:")
        for benchmark in candidate_only:
            print(f"- {benchmark}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare two search benchmark JSONL outputs and print metric deltas."
    )
    parser.add_argument("baseline", type=Path, help="Path to baseline benchmark JSONL file")
    parser.add_argument("candidate", type=Path, help="Path to candidate benchmark JSONL file")
    parser.add_argument(
        "--benchmarks",
        type=str,
        default="",
        help="Comma-separated benchmark names to include (default: all common benchmarks)",
    )
    parser.add_argument(
        "--show-missing",
        action="store_true",
        help="Print benchmark names present in only one file",
    )
    parser.add_argument(
        "--format",
        choices=("table", "markdown"),
        default="table",
        help="Output format for comparison rows",
    )

    args = parser.parse_args()

    if not args.baseline.exists():
        raise SystemExit(f"Baseline file not found: {args.baseline}")
    if not args.candidate.exists():
        raise SystemExit(f"Candidate file not found: {args.candidate}")

    baseline_records = _read_latest_records(args.baseline)
    candidate_records = _read_latest_records(args.candidate)

    include_benchmarks = {
        benchmark.strip()
        for benchmark in args.benchmarks.split(",")
        if benchmark and benchmark.strip()
    }
    if not include_benchmarks:
        include_benchmarks = None

    rows = list(
        _iter_rows(
            baseline_records=baseline_records,
            candidate_records=candidate_records,
            include_benchmarks=include_benchmarks,
        )
    )

    if not rows:
        print("No comparable benchmark metrics found.")
    else:
        headers = ["benchmark", "metric", "baseline", "candidate", "delta", "delta_pct", "status"]
        if args.format == "markdown":
            _print_markdown_table(headers=headers, rows=rows)
        else:
            _print_table(headers=headers, rows=rows)

    if args.show_missing:
        _print_missing(baseline_records, candidate_records)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
