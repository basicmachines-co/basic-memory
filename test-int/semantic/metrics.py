"""Quality metric computation and reporting for semantic search benchmarks.

Computes hit@1, recall@5, and MRR@10 from search results, plus timing
data for performance comparison across backends and providers.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from basic_memory.repository.search_index_row import SearchIndexRow


# --- Relevance helpers ---


def first_relevant_rank(results: list[SearchIndexRow], expected_topic: str, k: int) -> int | None:
    """Return the 1-based rank of the first result matching ``expected_topic``, or None."""
    expected_prefix = f"bench/{expected_topic}-"
    for rank, row in enumerate(results[:k], start=1):
        if (row.permalink or "").startswith(expected_prefix):
            return rank
    return None


# --- Metric dataclass ---


@dataclass
class QualityMetrics:
    """Aggregated quality metrics for a (combo, suite, mode) triple."""

    combo: str
    suite: str
    mode: str
    cases: int = 0
    hits_at_1: int = 0
    hits_at_5: int = 0
    reciprocal_rank_sum: float = 0.0
    per_query: list[dict] = field(default_factory=list)
    # Timing data: per-query latencies in seconds
    latencies: list[float] = field(default_factory=list)

    def record(
        self, query_text: str, expected_topic: str, rank: int | None, latency: float = 0.0
    ) -> None:
        self.cases += 1
        entry = {"query": query_text, "expected": expected_topic, "rank": rank}
        self.per_query.append(entry)
        self.latencies.append(latency)
        if rank is None:
            return
        self.reciprocal_rank_sum += 1.0 / rank
        if rank == 1:
            self.hits_at_1 += 1
        if rank <= 5:
            self.hits_at_5 += 1

    @property
    def hit_at_1(self) -> float:
        return self.hits_at_1 / self.cases if self.cases else 0.0

    @property
    def recall_at_5(self) -> float:
        return self.hits_at_5 / self.cases if self.cases else 0.0

    @property
    def mrr_at_10(self) -> float:
        return self.reciprocal_rank_sum / self.cases if self.cases else 0.0

    @property
    def total_time_ms(self) -> float:
        return sum(self.latencies) * 1000

    @property
    def avg_latency_ms(self) -> float:
        return mean(self.latencies) * 1000 if self.latencies else 0.0

    def as_dict(self) -> dict:
        return {
            "combo": self.combo,
            "suite": self.suite,
            "mode": self.mode,
            "cases": self.cases,
            "hit_at_1": round(self.hit_at_1, 4),
            "recall_at_5": round(self.recall_at_5, 4),
            "mrr_at_10": round(self.mrr_at_10, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "total_time_ms": round(self.total_time_ms, 2),
        }


# --- Comparison table ---


def format_comparison_table(all_metrics: list[QualityMetrics]) -> str:
    """Format a list of QualityMetrics into an ASCII comparison table."""
    header = (
        f"{'Combo':<25} {'Suite':<12} {'Mode':<8} "
        f"{'hit@1':>6} {'R@5':>6} {'MRR@10':>7} {'avg_ms':>8} {'total_ms':>9}"
    )
    separator = "-" * len(header)
    lines = [separator, header, separator]

    for m in sorted(all_metrics, key=lambda x: (x.suite, x.combo, x.mode)):
        lines.append(
            f"{m.combo:<25} {m.suite:<12} {m.mode:<8} "
            f"{m.hit_at_1:>6.3f} {m.recall_at_5:>6.3f} {m.mrr_at_10:>7.3f} "
            f"{m.avg_latency_ms:>8.1f} {m.total_time_ms:>9.1f}"
        )

    lines.append(separator)
    return "\n".join(lines)


# --- Artifact output ---


def write_benchmark_artifact(all_metrics: list[QualityMetrics]) -> None:
    """Append JSON-lines benchmark artifact if BASIC_MEMORY_BENCHMARK_OUTPUT is set."""
    output_path = os.getenv("BASIC_MEMORY_BENCHMARK_OUTPUT")
    if not output_path:
        return

    artifact_path = Path(output_path).expanduser()
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()

    with artifact_path.open("a", encoding="utf-8") as f:
        for m in all_metrics:
            payload = {
                "benchmark": f"semantic-quality-{m.combo}-{m.suite}-{m.mode}",
                "timestamp_utc": timestamp,
                "metrics": m.as_dict(),
            }
            f.write(json.dumps(payload, sort_keys=True) + "\n")
