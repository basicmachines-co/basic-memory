"""Fairness validation across provider runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from basic_memory_benchmarks.models import PerQueryRetrievalResult


def validate_fairness(
    results_by_provider: Mapping[str, Sequence[PerQueryRetrievalResult]],
) -> list[str]:
    """Validate that all providers were scored on the same query set.

    Returns a list of warnings. Empty list means no mismatch detected.
    """
    warnings: list[str] = []
    provider_names = sorted(results_by_provider.keys())
    if len(provider_names) < 2:
        return warnings

    baseline_provider = provider_names[0]
    baseline_ids = {row.query_id for row in results_by_provider[baseline_provider]}

    for provider in provider_names[1:]:
        current_ids = {row.query_id for row in results_by_provider[provider]}
        if baseline_ids != current_ids:
            missing = sorted(baseline_ids - current_ids)
            extra = sorted(current_ids - baseline_ids)
            warnings.append(
                f"Provider '{provider}' query mismatch: missing={missing[:5]} extra={extra[:5]}"
            )

    return warnings
