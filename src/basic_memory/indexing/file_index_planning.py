"""Portable file-index content read planning."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

type FileIndexPath = str
type FileIndexChecksum = str


@dataclass(frozen=True, slots=True)
class FileIndexTarget:
    """One file observed by an indexing coordinator."""

    path: FileIndexPath
    observed_checksum: FileIndexChecksum | None = None
    observed_size: int | None = None


class FileIndexDecisionStatus(StrEnum):
    """Decision for one file target before content is read."""

    read = "read"
    current = "current"
    missing = "missing"


@dataclass(frozen=True, slots=True)
class FileIndexDecision:
    """Decision for one file target."""

    path: FileIndexPath
    status: FileIndexDecisionStatus
    reason: str


@dataclass(frozen=True, slots=True)
class FileIndexPlan:
    """The content reads that remain after metadata checks."""

    paths_to_read: tuple[FileIndexPath, ...]
    decisions: tuple[FileIndexDecision, ...]


@dataclass(frozen=True, slots=True)
class FileIndexPlanSummary:
    """Decision counts for a file-index content read plan."""

    total_files: int
    files_to_read: int
    current_files: int
    missing_files: int


def current_file_index_decision(file_path: FileIndexPath) -> FileIndexDecision:
    """Return a no-op decision for an already-current file."""
    return FileIndexDecision(
        path=file_path,
        status=FileIndexDecisionStatus.current,
        reason=f"file already indexed: {file_path}",
    )


def plan_file_index_target_from_observed(
    target: FileIndexTarget,
    *,
    db_checksum: FileIndexChecksum | None,
) -> FileIndexDecision | None:
    """Use trusted observed metadata to skip content reads when possible."""
    if target.observed_checksum is not None and target.observed_checksum == db_checksum:
        return current_file_index_decision(target.path)
    return None


def plan_file_index_target_from_current(
    target: FileIndexTarget,
    *,
    db_checksum: FileIndexChecksum | None,
    current_checksum: FileIndexChecksum | None,
) -> FileIndexDecision:
    """Decide from current storage metadata after the observed shortcut misses."""
    if current_checksum is None:
        return FileIndexDecision(
            path=target.path,
            status=FileIndexDecisionStatus.missing,
            reason=f"file not found: {target.path}",
        )
    if current_checksum == db_checksum:
        return current_file_index_decision(target.path)
    return FileIndexDecision(
        path=target.path,
        status=FileIndexDecisionStatus.read,
        reason=f"file needs indexing: {target.path}",
    )


def build_file_index_plan(decisions: Iterable[FileIndexDecision]) -> FileIndexPlan:
    """Split read decisions from terminal no-op/missing decisions."""
    paths_to_read: list[FileIndexPath] = []
    terminal_decisions: list[FileIndexDecision] = []
    for decision in decisions:
        if decision.status == FileIndexDecisionStatus.read:
            paths_to_read.append(decision.path)
        else:
            terminal_decisions.append(decision)
    return FileIndexPlan(
        paths_to_read=tuple(paths_to_read),
        decisions=tuple(terminal_decisions),
    )


def summarize_file_index_plan(plan: FileIndexPlan) -> FileIndexPlanSummary:
    """Count read, current, and missing targets in a file-index plan."""
    current_files = sum(
        decision.status == FileIndexDecisionStatus.current for decision in plan.decisions
    )
    missing_files = sum(
        decision.status == FileIndexDecisionStatus.missing for decision in plan.decisions
    )
    files_to_read = len(plan.paths_to_read)
    return FileIndexPlanSummary(
        total_files=files_to_read + len(plan.decisions),
        files_to_read=files_to_read,
        current_files=current_files,
        missing_files=missing_files,
    )


def plan_legacy_file_index_targets(targets: Sequence[FileIndexTarget]) -> FileIndexPlan:
    """Plan content reads for old queue payloads that only carried file paths."""
    return FileIndexPlan(
        paths_to_read=tuple(target.path for target in targets),
        decisions=(),
    )
