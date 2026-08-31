"""Fairness validation across provider runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from basic_memory_benchmarks.models import PerQueryRetrievalResult


class HasTaskId(Protocol):
    """Any per-task result row (agent-task eval) carrying its task id."""

    @property
    def task_id(self) -> str: ...


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


def validate_surface_fairness(
    results_by_surface: Mapping[str, Sequence[HasTaskId]],
) -> list[str]:
    """Validate that all tool surfaces attempted the same task set.

    A skipped surface produces no rows and must be excluded by the caller (its
    status is already explicit); with fewer than two surfaces there is nothing
    to compare. Returns warnings; empty means no mismatch detected.
    """
    warnings: list[str] = []
    surface_names = sorted(results_by_surface.keys())
    if len(surface_names) < 2:
        return warnings

    baseline_surface = surface_names[0]
    baseline_ids = {row.task_id for row in results_by_surface[baseline_surface]}

    for surface in surface_names[1:]:
        current_ids = {row.task_id for row in results_by_surface[surface]}
        if baseline_ids != current_ids:
            missing = sorted(baseline_ids - current_ids)
            extra = sorted(current_ids - baseline_ids)
            warnings.append(
                f"Surface '{surface}' task mismatch vs '{baseline_surface}':"
                f" missing={missing[:5]} extra={extra[:5]}"
            )

    return warnings
