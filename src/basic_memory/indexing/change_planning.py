"""Portable project file change planning."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from basic_memory.indexing.file_index_planning import FileIndexChecksum, FileIndexPath


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


def plan_file_changes(
    *,
    storage_checksum_by_path: Mapping[FileIndexPath, FileIndexChecksum],
    db_checksum_by_path: Mapping[FileIndexPath, FileIndexChecksum],
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

    moved_files = plan_moved_files(
        new_file_checksum_by_path={path: storage_checksum_by_path[path] for path in new_files},
        storage_paths=storage_paths,
        move_candidates=move_candidates,
    )
    moved_new_paths = set(moved_files.values())
    moved_old_paths = set(moved_files)

    return ChangeReport(
        new_files=[path for path in new_files if path not in moved_new_paths],
        modified_files=modified_files,
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
