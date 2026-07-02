"""Portable project file change planning."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from basic_memory.indexing.file_index_planning import FileIndexChecksum, FileIndexPath


class StorageChecksumSource(Protocol):
    """Minimal storage-object metadata needed for change planning."""

    @property
    def checksum(self) -> FileIndexChecksum: ...


@dataclass(frozen=True, slots=True)
class FileMoveCandidate:
    """Existing indexed file that may correspond to a new storage path."""

    path: FileIndexPath
    checksum: FileIndexChecksum


@dataclass(frozen=True, slots=True)
class ChangeReport:
    """Results of change detection between storage and indexed DB state."""

    new_files: list[FileIndexPath] = field(default_factory=list)
    modified_files: list[FileIndexPath] = field(default_factory=list)
    deleted_files: list[FileIndexPath] = field(default_factory=list)
    moved_files: dict[FileIndexPath, FileIndexPath] = field(default_factory=dict)
    unchanged_files: list[FileIndexPath] = field(default_factory=list)

    @property
    def total_changes(self) -> int:
        """Total number of files that need processing."""
        return (
            len(self.new_files)
            + len(self.modified_files)
            + len(self.deleted_files)
            + len(self.moved_files)
        )

    @property
    def has_changes(self) -> bool:
        """Whether any changes were detected."""
        return self.total_changes > 0


@dataclass(frozen=True, slots=True)
class ChangeDetectionSnapshot:
    """Storage and indexed-DB state for one project change-detection pass."""

    storage_checksum_by_path: Mapping[FileIndexPath, FileIndexChecksum]
    db_checksum_by_path: Mapping[FileIndexPath, FileIndexChecksum | None]
    all_db_paths: tuple[FileIndexPath, ...]
    move_candidates: tuple[FileMoveCandidate, ...] = ()

    @property
    def storage_paths(self) -> tuple[FileIndexPath, ...]:
        """Return observed storage paths in adapter-provided order."""
        return tuple(self.storage_checksum_by_path)

    @property
    def new_file_checksum_by_path(self) -> dict[FileIndexPath, FileIndexChecksum]:
        """Return storage objects that do not have an indexed row at the same path."""
        return {
            path: checksum
            for path, checksum in self.storage_checksum_by_path.items()
            if path not in self.db_checksum_by_path
        }


def storage_checksums_from_sources(
    storage_files: Mapping[FileIndexPath, StorageChecksumSource],
) -> dict[FileIndexPath, FileIndexChecksum]:
    """Extract checksums from storage-object metadata without keeping vendor objects."""
    return {path: file_info.checksum for path, file_info in storage_files.items()}


def plan_change_detection_snapshot(snapshot: ChangeDetectionSnapshot) -> ChangeReport:
    """Classify changes from a typed runtime snapshot."""
    return plan_file_changes(
        storage_checksum_by_path=snapshot.storage_checksum_by_path,
        db_checksum_by_path=snapshot.db_checksum_by_path,
        all_db_paths=snapshot.all_db_paths,
        move_candidates=snapshot.move_candidates,
    )


def plan_file_changes(
    *,
    storage_checksum_by_path: Mapping[FileIndexPath, FileIndexChecksum],
    db_checksum_by_path: Mapping[FileIndexPath, FileIndexChecksum | None],
    all_db_paths: Sequence[FileIndexPath],
    move_candidates: Sequence[FileMoveCandidate],
) -> ChangeReport:
    """Classify storage-vs-DB file changes for one project."""
    storage_paths = set(storage_checksum_by_path)
    new_files: list[FileIndexPath] = []
    modified_files: list[FileIndexPath] = []
    unchanged_files: list[FileIndexPath] = []

    for path, storage_checksum in storage_checksum_by_path.items():
        db_checksum = db_checksum_by_path.get(path)
        if db_checksum is None:
            new_files.append(path)
            continue
        if storage_checksum != db_checksum:
            modified_files.append(path)
            continue
        unchanged_files.append(path)

    # Move destinations must be paths with no indexed row at all. A modified (or
    # null-checksum) path already has an entity; matching it to a deleted file's
    # checksum would redirect that entity onto the existing path and silently
    # drop the in-place edit.
    moved_files = plan_moved_files(
        new_file_checksum_by_path={
            path: storage_checksum_by_path[path]
            for path in new_files
            if path not in db_checksum_by_path
        },
        storage_paths=storage_paths,
        move_candidates=move_candidates,
    )
    moved_new_paths = set(moved_files.values())
    moved_old_paths = set(moved_files)

    return ChangeReport(
        new_files=[path for path in new_files if path not in moved_new_paths],
        modified_files=[path for path in modified_files if path not in moved_new_paths],
        deleted_files=sorted(set(all_db_paths) - storage_paths - moved_old_paths),
        moved_files=moved_files,
        unchanged_files=unchanged_files,
    )


def plan_moved_files(
    *,
    new_file_checksum_by_path: Mapping[FileIndexPath, FileIndexChecksum],
    storage_paths: set[FileIndexPath],
    move_candidates: Sequence[FileMoveCandidate],
) -> dict[FileIndexPath, FileIndexPath]:
    """Match new storage paths to missing indexed paths by checksum."""
    candidates_by_checksum: dict[FileIndexChecksum, list[FileMoveCandidate]] = {}
    for candidate in move_candidates:
        candidates_by_checksum.setdefault(candidate.checksum, []).append(candidate)

    moved_files: dict[FileIndexPath, FileIndexPath] = {}
    used_old_paths: set[FileIndexPath] = set()
    for new_path, checksum in new_file_checksum_by_path.items():
        for candidate in candidates_by_checksum.get(checksum, []):
            if candidate.path in storage_paths or candidate.path in used_old_paths:
                continue
            moved_files[candidate.path] = new_path
            used_old_paths.add(candidate.path)
            break
    return moved_files
