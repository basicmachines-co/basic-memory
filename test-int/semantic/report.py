"""Rich CLI viewer for semantic search benchmark JSONL artifacts.

Usage:
    python test-int/semantic/report.py .benchmarks/semantic-quality.jsonl
    python test-int/semantic/report.py .benchmarks/semantic-quality.jsonl --sort-by avg_latency_ms
    python test-int/semantic/report.py .benchmarks/semantic-quality.jsonl --filter-combo sqlite
    python test-int/semantic/report.py .benchmarks/semantic-quality.jsonl --filter-suite paraphrase
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text


def load_benchmarks(path: Path) -> list[dict]:
    """Load benchmark records from a JSONL file."""
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _quality_cell(value: float) -> Text:
    """Color-code a quality metric value."""
    text = f"{value:.3f}"
    if value >= 0.90:
        return Text(text, style="bold green")
    if value >= 0.75:
        return Text(text, style="green")
    if value >= 0.50:
        return Text(text, style="yellow")
    if value > 0.0:
        return Text(text, style="red")
    return Text(text, style="dim red")


def _latency_cell(value: float) -> Text:
    """Color-code a latency value (ms)."""
    text = f"{value:.1f}"
    if value <= 5.0:
        return Text(text, style="bold green")
    if value <= 20.0:
        return Text(text, style="green")
    if value <= 100.0:
        return Text(text, style="yellow")
    return Text(text, style="red")


SORT_KEYS = {
    "combo": lambda r: r["metrics"]["combo"],
    "suite": lambda r: r["metrics"]["suite"],
    "mode": lambda r: r["metrics"]["mode"],
    "hit_at_1": lambda r: -r["metrics"].get("hit_at_1", 0),
    "recall_at_5": lambda r: -r["metrics"].get("recall_at_5", 0),
    "mrr_at_10": lambda r: -r["metrics"].get("mrr_at_10", 0),
    "avg_latency_ms": lambda r: r["metrics"].get("avg_latency_ms", 0),
    "total_time_ms": lambda r: r["metrics"].get("total_time_ms", 0),
}


def build_table(records: list[dict], title: str = "Semantic Search Benchmarks") -> Table:
    """Build a rich Table from benchmark records."""
    table = Table(title=title, show_lines=False)
    table.add_column("Combo", style="cyan", no_wrap=True)
    table.add_column("Suite", style="magenta", no_wrap=True)
    table.add_column("Mode", style="blue", no_wrap=True)
    table.add_column("N", justify="right")
    table.add_column("hit@1", justify="right", no_wrap=True)
    table.add_column("R@5", justify="right", no_wrap=True)
    table.add_column("MRR@10", justify="right", no_wrap=True)
    table.add_column("avg ms", justify="right", no_wrap=True)
    table.add_column("total ms", justify="right", no_wrap=True)

    for rec in records:
        m = rec["metrics"]
        table.add_row(
            m["combo"],
            m["suite"],
            m["mode"],
            str(m["cases"]),
            _quality_cell(m.get("hit_at_1", 0)),
            _quality_cell(m.get("recall_at_5", 0)),
            _quality_cell(m.get("mrr_at_10", 0)),
            _latency_cell(m.get("avg_latency_ms", 0)),
            _latency_cell(m.get("total_time_ms", 0)),
        )

    return table


def build_summary_table(records: list[dict]) -> Table:
    """Build a summary table comparing combos across suites."""
    # Group by combo
    combos: dict[str, dict[str, dict]] = {}
    for rec in records:
        m = rec["metrics"]
        key = m["combo"]
        if key not in combos:
            combos[key] = {}
        suite_mode = f"{m['suite']}/{m['mode']}"
        combos[key][suite_mode] = m

    table = Table(title="Summary: Best Recall@5 by Combo", show_lines=False)
    table.add_column("Combo", style="cyan", no_wrap=True)
    table.add_column("Lexical (best)", justify="right")
    table.add_column("Paraphrase (best)", justify="right")
    table.add_column("Avg Latency (best)", justify="right")

    for combo_name, suite_modes in sorted(combos.items()):
        # Find best recall@5 for lexical and paraphrase
        lexical_best = max(
            (v.get("recall_at_5", 0) for k, v in suite_modes.items() if k.startswith("lexical")),
            default=0,
        )
        paraphrase_best = max(
            (v.get("recall_at_5", 0) for k, v in suite_modes.items() if k.startswith("paraphrase")),
            default=0,
        )
        # Find best (lowest) avg latency
        avg_latencies = [
            v.get("avg_latency_ms", 0)
            for v in suite_modes.values()
            if v.get("avg_latency_ms", 0) > 0
        ]
        best_latency = min(avg_latencies) if avg_latencies else 0

        table.add_row(
            combo_name,
            _quality_cell(lexical_best),
            _quality_cell(paraphrase_best),
            _latency_cell(best_latency),
        )

    return table


def main():
    parser = argparse.ArgumentParser(description="View semantic search benchmark results")
    parser.add_argument("path", type=Path, help="Path to JSONL benchmark artifact")
    parser.add_argument(
        "--sort-by", choices=list(SORT_KEYS.keys()), default="combo", help="Sort column"
    )
    parser.add_argument(
        "--filter-combo", type=str, default=None, help="Filter by combo name (substring match)"
    )
    parser.add_argument(
        "--filter-suite", type=str, default=None, help="Filter by suite name (substring match)"
    )
    parser.add_argument(
        "--filter-mode", type=str, default=None, help="Filter by mode (fts, vector, hybrid)"
    )
    parser.add_argument("--no-summary", action="store_true", help="Skip the summary table")

    args = parser.parse_args()

    if not args.path.exists():
        print(f"File not found: {args.path}", file=sys.stderr)
        sys.exit(1)

    records = load_benchmarks(args.path)
    if not records:
        print("No benchmark records found.", file=sys.stderr)
        sys.exit(1)

    # Apply filters
    if args.filter_combo:
        records = [r for r in records if args.filter_combo in r["metrics"]["combo"]]
    if args.filter_suite:
        records = [r for r in records if args.filter_suite in r["metrics"]["suite"]]
    if args.filter_mode:
        records = [r for r in records if args.filter_mode == r["metrics"]["mode"]]

    if not records:
        print("No records match the filters.", file=sys.stderr)
        sys.exit(1)

    # Sort
    sort_fn = SORT_KEYS.get(args.sort_by, SORT_KEYS["combo"])
    records.sort(key=sort_fn)

    console = Console(width=max(120, Console().width))

    # Timestamp from first record
    timestamp = records[0].get("timestamp_utc", "unknown")
    console.print(f"\n[dim]Benchmark run: {timestamp}[/dim]")
    console.print(f"[dim]Records: {len(records)}[/dim]\n")

    # Detail table
    console.print(build_table(records))

    # Summary table
    if not args.no_summary:
        console.print()
        console.print(build_summary_table(records))

    console.print()


if __name__ == "__main__":
    main()
