"""Answerer-vs-retrieval failure attribution.

The QA pipeline is: provider retrieves → a FIXED answerer generates from the
retrieved context → a FIXED judge grades vs gold. Because the answerer is held
constant across providers, an absolute QA accuracy of, say, 0.75 conflates two
very different things:

  * retrieval found the gold evidence but the answerer didn't use it correctly
    ("retrieved but unused" — an answerer failure, identical for every provider
    given the same retrieved context), versus
  * retrieval never surfaced the gold evidence ("truly missed" — a real
    retrieval failure, the thing this project actually optimizes).

This module joins the QA verdicts with the retrieval rows on
``(provider, query_id)`` and splits each answerable failure into those two
buckets. It turns "BM QA is 0.75" into "BM retrieval ceiling is 1.00; the 0.25
gap is the answerer, not retrieval" — which is the honest, scrutiny-proof way to
report what retrieval improvements can and cannot move.

Unanswerable items (empty ``ground_truth`` — the abstention set) have no
retrieval target, so they are counted separately and excluded from the
attribution.
"""

from __future__ import annotations

from basic_memory_benchmarks.models import (
    CategoryDiagnosis,
    PerQueryRetrievalResult,
    ProviderDiagnosis,
    QACaseResult,
)


def _recall_value(row: PerQueryRetrievalResult, recall_field: str) -> float:
    return float(getattr(row, recall_field))


def diagnose_provider(
    qa_cases: list[QACaseResult],
    retrieval_rows: list[PerQueryRetrievalResult],
    *,
    provider: str,
    recall_field: str = "recall_at_10",
) -> ProviderDiagnosis:
    """Attribute one provider's QA outcomes to retrieval vs the answerer.

    ``qa_cases`` and ``retrieval_rows`` may contain rows for other providers;
    only those matching ``provider`` are considered. A question counts as
    "retrieved" when its joined retrieval row has ``recall_field`` > 0.
    """
    recall_by_qid: dict[str, float] = {
        row.query_id: _recall_value(row, recall_field)
        for row in retrieval_rows
        if row.provider == provider
    }

    diag = ProviderDiagnosis(provider=provider, recall_field=recall_field)
    by_cat: dict[str, CategoryDiagnosis] = {}

    for case in qa_cases:
        if case.provider != provider:
            continue
        diag.total_cases += 1

        if case.error:
            diag.errored += 1
            continue

        # Unanswerable / abstention items carry no retrieval ground truth.
        retrieval_row = next(
            (r for r in retrieval_rows if r.provider == provider and r.query_id == case.query_id),
            None,
        )
        if retrieval_row is not None and not retrieval_row.ground_truth:
            diag.unanswerable += 1
            continue
        if retrieval_row is None and case.query_id not in recall_by_qid:
            diag.unmatched += 1
            continue

        diag.answerable += 1
        cat = by_cat.setdefault(case.category, CategoryDiagnosis())
        cat.answerable += 1

        if case.correct:
            diag.correct += 1
            cat.correct += 1
            continue

        # Wrong answer: attribute to retrieval (gold not found) or answerer.
        retrieved = recall_by_qid.get(case.query_id, 0.0) > 0
        if retrieved:
            diag.retrieved_but_unused += 1
            cat.retrieved_but_unused += 1
        else:
            diag.truly_missed += 1
            cat.truly_missed += 1

    _finalize(diag)
    diag.by_category = dict(sorted(by_cat.items()))
    return diag


def _finalize(diag: ProviderDiagnosis) -> None:
    answerable = diag.answerable
    if answerable:
        diag.qa_accuracy = diag.correct / answerable
        diag.retrieval_ceiling = (diag.correct + diag.retrieved_but_unused) / answerable
        diag.answerer_gap = diag.retrieved_but_unused / answerable
        diag.retrieval_gap = diag.truly_missed / answerable
    failures = diag.retrieved_but_unused + diag.truly_missed
    if failures:
        diag.answerer_failure_share = diag.retrieved_but_unused / failures


def diagnose_run(
    qa_cases: list[QACaseResult],
    retrieval_rows: list[PerQueryRetrievalResult],
    *,
    recall_field: str = "recall_at_10",
) -> list[ProviderDiagnosis]:
    """Diagnose every provider present in ``qa_cases`` (stable provider order)."""
    providers: list[str] = []
    seen: set[str] = set()
    for case in qa_cases:
        if case.provider not in seen:
            seen.add(case.provider)
            providers.append(case.provider)
    return [
        diagnose_provider(qa_cases, retrieval_rows, provider=provider, recall_field=recall_field)
        for provider in providers
    ]
