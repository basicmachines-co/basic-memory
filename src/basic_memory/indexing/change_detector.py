"""Portable async orchestration for project file change detection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import logfire
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.indexing.change_planning import (
    ChangeDetectionSnapshot,
    ChangeReport,
    FileMoveCandidate,
    StorageChecksumSource,
    plan_change_detection_snapshot,
    storage_checksums_from_sources,
)
from basic_memory.indexing.file_index_planning import FileIndexChecksum, FileIndexPath
from basic_memory.repository import EntityRepository


class ChangeDetectionLogger(Protocol):
    """Logger shape used by change-detection orchestration."""

    def debug(self, message: str) -> object: ...

    def info(self, message: str) -> object: ...


class ChangeDetectionStore(Protocol):
    """Indexed project state needed to classify storage changes."""

    async def load_indexed_file_checksums(
        self,
        paths: tuple[FileIndexPath, ...],
    ) -> Mapping[FileIndexPath, FileIndexChecksum | None]: ...

    async def load_all_indexed_paths(self) -> tuple[FileIndexPath, ...]: ...

    async def load_move_candidates(
        self,
        new_file_checksums: Mapping[FileIndexPath, FileIndexChecksum],
    ) -> tuple[FileMoveCandidate, ...]: ...


@dataclass(frozen=True, slots=True)
class ChangeDetector:
    """Repository-backed project file change detector."""

    entity_repository: EntityRepository
    session_maker: async_sessionmaker[AsyncSession]

    async def detect_all_changes(
        self,
        storage_files: Mapping[FileIndexPath, StorageChecksumSource],
        bound_logger: ChangeDetectionLogger | None = None,
    ) -> ChangeReport:
        """Detect all storage-vs-database changes for this repository's project."""
        return await detect_project_file_changes(
            storage_files,
            store=self,
            bound_logger=bound_logger,
        )

    async def load_indexed_file_checksums(
        self,
        paths: tuple[FileIndexPath, ...],
    ) -> dict[FileIndexPath, FileIndexChecksum | None]:
        """Load indexed checksums for project-relative file paths."""
        if not paths:
            return {}

        async with db.scoped_session(self.session_maker) as session:
            rows = await self.entity_repository.get_by_file_paths(session, paths)

        return {str(row[0]): str(row[1]) if row[1] is not None else None for row in rows}

    async def load_move_candidates(
        self,
        new_file_checksums: Mapping[FileIndexPath, FileIndexChecksum],
    ) -> tuple[FileMoveCandidate, ...]:
        """Load indexed entities that can prove an observed path is a move."""
        if not new_file_checksums:
            return ()

        checksums = sorted(set(new_file_checksums.values()))
        async with db.scoped_session(self.session_maker) as session:
            candidates = await self.entity_repository.find_by_checksums(session, checksums)

        return tuple(
            FileMoveCandidate(path=str(candidate.file_path), checksum=str(candidate.checksum))
            for candidate in candidates
            if candidate.checksum
        )

    async def load_all_indexed_paths(self) -> tuple[FileIndexPath, ...]:
        """Load all indexed file paths for delete planning."""
        async with db.scoped_session(self.session_maker) as session:
            paths = await self.entity_repository.get_all_file_paths(session)
        return tuple(str(path) for path in paths)


async def detect_project_file_changes(
    storage_files: Mapping[FileIndexPath, StorageChecksumSource],
    *,
    store: ChangeDetectionStore,
    bound_logger: ChangeDetectionLogger | None = None,
) -> ChangeReport:
    """Detect project file changes from storage metadata and indexed state."""
    log = bound_logger or logger

    with logfire.span(
        "change_detector.detect_all_changes",
        s3_file_count=len(storage_files),
    ):
        storage_checksum_by_path = storage_checksums_from_sources(storage_files)
        storage_paths = tuple(storage_checksum_by_path)

        with logfire.span("change_detector.get_db_checksums", path_count=len(storage_paths)):
            db_checksums = await store.load_indexed_file_checksums(storage_paths)
            log.debug(f"[CHANGE] Fetched {len(db_checksums)} checksums from DB")

        with logfire.span("change_detector.detect_deletes"):
            all_db_paths = await store.load_all_indexed_paths()

        candidate_snapshot = ChangeDetectionSnapshot(
            storage_checksum_by_path=storage_checksum_by_path,
            db_checksum_by_path=db_checksums,
            all_db_paths=all_db_paths,
        )
        with logfire.span(
            "change_detector.detect_moves",
            candidate_count=len(candidate_snapshot.new_file_checksum_by_path),
        ):
            move_candidates = await store.load_move_candidates(
                candidate_snapshot.new_file_checksum_by_path
            )

        snapshot = ChangeDetectionSnapshot(
            storage_checksum_by_path=storage_checksum_by_path,
            db_checksum_by_path=db_checksums,
            all_db_paths=all_db_paths,
            move_candidates=move_candidates,
        )
        report = plan_change_detection_snapshot(snapshot)

        for old_path, new_path in report.moved_files.items():
            log.debug(f"[CHANGE] Detected move: {old_path} -> {new_path}")
        log.info(f"[CHANGE] Move detection: found {len(report.moved_files)} moved files")
        log.info(f"[CHANGE] Delete detection: found {len(report.deleted_files)} deleted files")
        log.info(
            f"[CHANGE] Detection complete: {len(report.new_files)} new, "
            f"{len(report.modified_files)} modified, "
            f"{len(report.deleted_files)} deleted, {len(report.moved_files)} moved, "
            f"{len(report.unchanged_files)} unchanged"
        )

        return report
