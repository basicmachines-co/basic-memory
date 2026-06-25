"""Optional judge scoring.

Primary mode tries Pydantic Evals. If unavailable or misconfigured, it falls back to
an explicit deterministic checker and records the reason.
"""

from __future__ import annotations

import os
from typing import Any

from basic_memory_benchmarks.models import JudgeCaseResult, JudgeSummary, PerQueryRetrievalResult


def _deterministic_contains_eval(
    rows: list[PerQueryRetrievalResult], provider: str
) -> tuple[list[JudgeCaseResult], JudgeSummary]:
    case_results: list[JudgeCaseResult] = []
    for row in rows:
        expected = (row.expected_answer or "").strip().lower()
        context = row.retrieved_context.strip().lower()
        passed = bool(expected and expected in context)
        case_results.append(
            JudgeCaseResult(
                provider=provider,
                query_id=row.query_id,
                category=row.category,
                passed=passed,
                score=1.0 if passed else 0.0,
                reason="Deterministic contains check",
                evaluator="deterministic-fallback",
            )
        )

    pass_count = sum(1 for item in case_results if item.passed)
    total = len(case_results)
    summary = JudgeSummary(
        provider=provider,
        evaluator="deterministic-fallback",
        model="none",
        total_cases=total,
        pass_count=pass_count,
        accuracy=(pass_count / total) if total else 0.0,
    )
    return case_results, summary


def run_optional_judge(
    rows: list[PerQueryRetrievalResult],
    provider: str,
    model: str,
) -> tuple[list[JudgeCaseResult], JudgeSummary]:
    """Run optional judge scoring.

    If pydantic-evals + OpenAI credentials are available, we attempt to use it.
    Otherwise we return deterministic fallback scores.
    """
    relevant_rows = [row for row in rows if row.expected_answer]
    if not relevant_rows:
        return [], JudgeSummary(
            provider=provider,
            evaluator="none",
            model=model,
            total_cases=0,
            pass_count=0,
            accuracy=0.0,
            skipped_reason="No expected answers available for judge scoring",
        )

    if not os.getenv("OPENAI_API_KEY"):
        case_results, summary = _deterministic_contains_eval(relevant_rows, provider)
        summary.skipped_reason = "OPENAI_API_KEY missing; used deterministic fallback"
        return case_results, summary

    try:
        from pydantic_evals import Case, Dataset  # type: ignore
        from pydantic_evals.evaluators import LLMJudge  # type: ignore
    except Exception:
        case_results, summary = _deterministic_contains_eval(relevant_rows, provider)
        summary.skipped_reason = "pydantic-evals not installed; used deterministic fallback"
        return case_results, summary

    # Best-effort pydantic-evals integration.
    # Trigger: runtime has judge deps + API key
    # Why: align with competitor methodology
    # Outcome: if API shape changes, fallback remains deterministic and explicit
    try:
        cases: list[Any] = []
        for row in relevant_rows:
            rubric = (
                "PASS if the candidate response contains the same core factual answer as expected. "
                "FAIL if key facts are missing or contradictory."
            )
            candidate = row.retrieved_context
            expected = row.expected_answer or ""
            cases.append(
                Case(
                    name=row.query_id,
                    inputs=f"question: {row.query_text}\nexpected: {expected}\ncandidate: {candidate}",
                    evaluators=[LLMJudge(rubric=rubric, include_input=True, model=model)],
                    metadata={"provider": provider, "category": row.category},
                )
            )

        dataset = Dataset(cases=cases)
        report = dataset.evaluate_sync(lambda value: value)

        case_results: list[JudgeCaseResult] = []
        pass_count = 0
        for idx, row in enumerate(relevant_rows):
            case_report = report.cases[idx]
            passed = bool(getattr(case_report, "passed", False))
            pass_count += 1 if passed else 0
            reason = str(getattr(case_report, "reason", "")) or "LLM judge result"
            case_results.append(
                JudgeCaseResult(
                    provider=provider,
                    query_id=row.query_id,
                    category=row.category,
                    passed=passed,
                    score=1.0 if passed else 0.0,
                    reason=reason,
                    evaluator="pydantic-evals-llmjudge",
                )
            )

        total = len(case_results)
        summary = JudgeSummary(
            provider=provider,
            evaluator="pydantic-evals-llmjudge",
            model=model,
            total_cases=total,
            pass_count=pass_count,
            accuracy=(pass_count / total) if total else 0.0,
        )
        return case_results, summary
    except Exception:
        case_results, summary = _deterministic_contains_eval(relevant_rows, provider)
        summary.skipped_reason = "pydantic-evals execution failed; used deterministic fallback"
        return case_results, summary
