from basic_memory_benchmarks.fairness import validate_fairness
from basic_memory_benchmarks.models import PerQueryRetrievalResult


def _row(provider: str, query_id: str) -> PerQueryRetrievalResult:
    return PerQueryRetrievalResult(
        provider=provider,
        query_id=query_id,
        query_text="q",
        category="single_hop",
        category_id=1,
        ground_truth=[],
        expected_answer=None,
        hits=[],
        recall_at_5=0.0,
        recall_at_10=0.0,
        precision_at_5=0.0,
        mrr=0.0,
        content_hit=False,
        latency_ms=1.0,
    )


def test_validate_fairness_ok() -> None:
    warnings = validate_fairness(
        {
            "a": [_row("a", "q1")],
            "b": [_row("b", "q1")],
        }
    )
    assert warnings == []


def test_validate_fairness_mismatch() -> None:
    warnings = validate_fairness(
        {
            "a": [_row("a", "q1")],
            "b": [_row("b", "q2")],
        }
    )
    assert len(warnings) == 1
