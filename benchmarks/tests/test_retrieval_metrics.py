from basic_memory_benchmarks.models import QueryCase, SearchHit
from basic_memory_benchmarks.scoring.retrieval import (
    evaluate_query,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    summarize_provider,
)


def test_metric_primitives() -> None:
    hits = [
        SearchHit(source_doc_id="doc-a", text="A", score=0.9),
        SearchHit(source_doc_id="doc-b", text="B", score=0.8),
    ]
    truth = {"doc-a"}
    assert recall_at_k(hits, truth, 5) == 1.0
    assert precision_at_k(hits, truth, 5) == 0.5
    assert reciprocal_rank(hits, truth) == 1.0


def test_evaluate_and_summary() -> None:
    query = QueryCase(
        id="q1",
        query="question",
        category="single_hop",
        category_id=1,
        ground_truth=["doc-a"],
        expected_answer="token",
    )
    hits = [
        SearchHit(source_doc_id="doc-a", text="token refresh", score=0.9),
        SearchHit(source_doc_id="doc-x", text="other", score=0.2),
    ]

    row = evaluate_query(provider="bm-local", query=query, hits=hits, latency_ms=10.0)
    assert row.recall_at_5 == 1.0
    assert row.content_hit is True

    summary = summarize_provider("bm-local", [row])
    assert summary.metrics.recall_at_5 == 1.0
    assert summary.official_headline.query_count == 1
