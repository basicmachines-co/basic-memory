"""Portable file-index content read planning."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from basic_memory.runtime.jobs import RuntimeIndexFileBatchJobRequest, RuntimeObservedIndexFile
from basic_memory.runtime.storage import StorageEtag, normalize_storage_etag

type FileIndexPath = str
type FileIndexChecksum = str


@dataclass(frozen=True, slots=True)
class IndexedChecksums:
    """What the index knows about one path's stored file.

    `checksum` is the file as indexed. When indexing rewrote the file (adding frontmatter or
    a permalink), `sync_checksum` is the file as it was synced in before that rewrite. A sync
    client that copies its original over our rewrite is sending a file we have already
    indexed, so it must not trigger another index and rewrite: that is an endless loop with
    any one-way sync.
    """

    checksum: FileIndexChecksum | None
    sync_checksum: FileIndexChecksum | None = None
    # The content checksum of the note's accepted Markdown, for notes written DB-first.
    accepted_content_checksum: FileIndexChecksum | None = None

    def recognizes(self, storage_checksum: FileIndexChecksum | None) -> bool:
        """Whether a stored file with `storage_checksum` is already indexed.

        A row without a checksum is incomplete and must be read again whatever the sync
        checksum says.
        """
        if self.checksum is None or storage_checksum is None:
            return False
        return storage_checksum in (self.checksum, self.sync_checksum)

    def holds_accepted_content(self, content_checksum: FileIndexChecksum | None) -> bool:
        """Whether a stored file whose content hashes to `content_checksum` is the accepted note.

        A DB-first write indexes its accepted content before the file is written, and its
        storage checksum is recorded only after the write, so the file's own storage
        notification can arrive first, including for a brand-new note whose storage
        checksum is still unset. Matching the content closes that gap. The indexed-checksum
        lookup withholds the accepted checksum from an incomplete row (pending graph
        publication), which is therefore still read.
        """
        if content_checksum is None:
            return False
        return content_checksum == self.accepted_content_checksum


@dataclass(frozen=True, slots=True)
class FileIndexTarget:
    """One file observed by an indexing coordinator."""

    path: FileIndexPath
    observed_checksum: FileIndexChecksum | None = None
    observed_size: int | None = None

    @classmethod
    def from_observed_storage_object(
        cls,
        *,
        path: FileIndexPath,
        etag: StorageEtag,
        size: int | None = None,
    ) -> Self:
        return cls(
            path=path,
            observed_checksum=normalize_storage_etag(etag),
            observed_size=size,
        )

    @classmethod
    def from_runtime_observed_file(cls, observed_file: RuntimeObservedIndexFile) -> Self:
        """Convert runtime project-index metadata into checker input."""
        return cls(
            path=observed_file.path,
            observed_checksum=observed_file.checksum,
            observed_size=observed_file.size,
        )


def file_index_targets_from_runtime_batch_request(
    request: RuntimeIndexFileBatchJobRequest,
) -> tuple[FileIndexTarget, ...]:
    """Return checker targets for a runtime batch request."""
    if request.observed_files:
        return tuple(
            FileIndexTarget.from_runtime_observed_file(observed_file)
            for observed_file in request.observed_files
        )
    return tuple(FileIndexTarget(path=file_path) for file_path in request.file_paths)


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


def move_orphan_file_index_decision(
    file_path: FileIndexPath,
    *,
    indexed_at: FileIndexPath | None,
) -> FileIndexDecision:
    """Return a no-op decision for a move's still-present source object.

    A move updates the note's entity to the destination path, but the physical source object can
    linger (deferred or skipped cleanup). Indexing that vacated path finds no entity there and would
    mint a brand-new note whose permalink collides into ``-1`` — the ghost duplicate
    (basic-memory-cloud#1601). When the object's content is already indexed at another path, treat
    it as current (already represented) instead of creating a duplicate.
    """
    reason = (
        f"move orphan: content already indexed at {indexed_at}; not creating a duplicate"
        if indexed_at is not None
        else "move orphan tombstone: deleted note's vacated source is pending cleanup"
    )
    return FileIndexDecision(
        path=file_path,
        status=FileIndexDecisionStatus.current,
        reason=reason,
    )


def plan_file_index_target_from_observed(
    target: FileIndexTarget,
    *,
    indexed: IndexedChecksums | None,
) -> FileIndexDecision | None:
    """Use trusted observed metadata to skip content reads when possible."""
    if indexed is not None and indexed.recognizes(target.observed_checksum):
        return current_file_index_decision(target.path)
    return None


def plan_file_index_target_from_current(
    target: FileIndexTarget,
    *,
    indexed: IndexedChecksums | None,
    current_checksum: FileIndexChecksum | None,
) -> FileIndexDecision:
    """Decide from current storage metadata after the observed shortcut misses."""
    if current_checksum is None:
        return FileIndexDecision(
            path=target.path,
            status=FileIndexDecisionStatus.missing,
            reason=f"file not found: {target.path}",
        )
    if indexed is not None and indexed.recognizes(current_checksum):
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
