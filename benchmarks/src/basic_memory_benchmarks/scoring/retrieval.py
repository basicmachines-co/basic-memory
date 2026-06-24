"""Deterministic retrieval metric calculations."""

from __future__ import annotations

import math
from pathlib import Path
from statistics import mean

from basic_memory_benchmarks.models import (
    PerQueryRetrievalResult,
    QueryCase,
    RetrievalMetrics,
    RetrievalSummary,
    SearchHit,
)


OFFICIAL_HEADLINE_CATEGORIES = {"single_hop", "multi_hop", "temporal", "open_domain"}


def _basename(identifier: str) -> str:
    return Path(identifier).name.rsplit(".", 1)[0]


def is_match(hit: SearchHit, ground_truth: set[str]) -> bool:
    """Return whether a search hit matches any ground-truth doc id."""
    candidates = set(ground_truth)
    if hit.source_doc_id:
        if hit.source_doc_id in candidates:
            return True
        if _basename(hit.source_doc_id) in {_basename(item) for item in candidates}:
            return True

    if hit.source_path:
        hit_name = _basename(hit.source_path)
        for item in candidates:
            if hit_name == _basename(item):
                return True

    return False


def recall_at_k(hits: list[SearchHit], ground_truth: set[str], k: int) -> float:
    if not ground_truth:
        return 0.0
    top_hits = hits[:k]
    matched = {truth for truth in ground_truth if any(is_match(hit, {truth}) for hit in top_hits)}
    return len(matched) / len(ground_truth)


def precision_at_k(hits: list[SearchHit], ground_truth: set[str], k: int) -> float:
    if k <= 0:
        return 0.0
    top_hits = hits[:k]
    if not top_hits:
        return 0.0
    relevant = sum(1 for hit in top_hits if is_match(hit, ground_truth))
    return relevant / len(top_hits)


def reciprocal_rank(hits: list[SearchHit], ground_truth: set[str]) -> float:
    for index, hit in enumerate(hits, start=1):
        if is_match(hit, ground_truth):
            return 1.0 / index
    return 0.0


def content_hit(expected_answer: str | None, hits: list[SearchHit]) -> bool:
    if not expected_answer:
        return False
    needle = expected_answer.strip().lower()
    if not needle:
        return False
    haystack = "\n".join((hit.text or "") for hit in hits).lower()
    return needle in haystack


def evaluate_query(
    *,
    provider: str,
    query: QueryCase,
    hits: list[SearchHit],
    latency_ms: float,
) -> PerQueryRetrievalResult:
    ground_truth = set(query.ground_truth)
    rec5 = recall_at_k(hits, ground_truth, 5)
    rec10 = recall_at_k(hits, ground_truth, 10)
    prec5 = precision_at_k(hits, ground_truth, 5)
    mrr = reciprocal_rank(hits, ground_truth)
    ch = content_hit(query.expected_answer, hits)
    top_hit_doc_id = hits[0].source_doc_id if hits else None
    context = "\n".join((hit.text or "") for hit in hits[:5]).strip()

    return PerQueryRetrievalResult(
        provider=provider,
        query_id=query.id,
        query_text=query.query,
        category=query.category,
        category_id=query.category_id,
        ground_truth=query.ground_truth,
        expected_answer=query.expected_answer,
        hits=hits,
        recall_at_5=rec5,
        recall_at_10=rec10,
        precision_at_5=prec5,
        mrr=mrr,
        content_hit=ch,
        latency_ms=latency_ms,
        top_hit_doc_id=top_hit_doc_id,
        retrieved_context=context,
        metadata=query.metadata,
    )


def _aggregate_metrics(rows: list[PerQueryRetrievalResult]) -> RetrievalMetrics:
    if not rows:
        return RetrievalMetrics()

    latencies = [row.latency_ms for row in rows]
    latencies_sorted = sorted(latencies)
    p95_index = max(0, min(len(latencies_sorted) - 1, math.ceil(len(latencies_sorted) * 0.95) - 1))

    return RetrievalMetrics(
        recall_at_5=mean(row.recall_at_5 for row in rows),
        recall_at_10=mean(row.recall_at_10 for row in rows),
        precision_at_5=mean(row.precision_at_5 for row in rows),
        mrr=mean(row.mrr for row in rows),
        content_hit_rate=mean(1.0 if row.content_hit else 0.0 for row in rows),
        mean_latency_ms=mean(latencies),
        p95_latency_ms=latencies_sorted[p95_index],
        query_count=len(rows),
    )


def summarize_provider(provider: str, rows: list[PerQueryRetrievalResult]) -> RetrievalSummary:
    by_category: dict[str, list[PerQueryRetrievalResult]] = {}
    for row in rows:
        by_category.setdefault(row.category, []).append(row)

    category_metrics = {
        category: _aggregate_metrics(group) for category, group in by_category.items()
    }

    official_rows = [
        row
        for row in rows
        if row.category in OFFICIAL_HEADLINE_CATEGORIES or row.category_id in {1, 2, 3, 4}
    ]
    adversarial_rows = [
        row for row in rows if row.category == "adversarial" or row.category_id == 5
    ]

    return RetrievalSummary(
        provider=provider,
        metrics=_aggregate_metrics(rows),
        by_category=category_metrics,
        official_headline=_aggregate_metrics(official_rows),
        adversarial_breakout=_aggregate_metrics(adversarial_rows),
    )
