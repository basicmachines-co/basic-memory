"""Portable async file-index metadata checking."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.file_index_planning import (
    FileIndexChecksum,
    FileIndexDecision,
    FileIndexPath,
    FileIndexPlan,
    FileIndexTarget,
    build_file_index_plan,
    plan_file_index_target_from_current,
    plan_file_index_target_from_observed,
    plan_legacy_file_index_targets,
)
from basic_memory.services.exceptions import FileOperationError


class IndexedFileChecksumSource(Protocol):
    """Capability that reads accepted checksums from the indexing store."""

    async def load_indexed_file_checksums(
        self,
        file_paths: Sequence[FileIndexPath],
    ) -> Mapping[FileIndexPath, FileIndexChecksum | None]:
        """Return indexed checksums keyed by file path."""


class CurrentFileChecksumSource(Protocol):
    """Capability that reads current file checksums from storage metadata."""

    async def load_current_file_checksum(
        self,
        file_path: FileIndexPath,
    ) -> FileIndexChecksum | None:
        """Return the current checksum for one file, or None when it is missing."""


class IndexedFileChecksumRow(Protocol):
    """Tuple-like row containing file path and indexed checksum."""

    def __getitem__(self, index: int, /) -> object:
        """Return a row field by positional index."""


class IndexedFileChecksumRepository(Protocol):
    """Repository capability that loads accepted checksums for file paths."""

    async def get_by_file_paths(
        self,
        session: AsyncSession,
        file_paths: Sequence[FileIndexPath],
    ) -> Sequence[IndexedFileChecksumRow]:
        """Return rows whose first two fields are file path and checksum."""


class CurrentFileMetadata(Protocol):
    """Storage metadata shape needed for file-index current checksum checks."""

    @property
    def checksum(self) -> FileIndexChecksum:
        """Return the current storage checksum."""


class CurrentFileMetadataSource(Protocol):
    """Capability that loads current storage metadata for one file path."""

    async def load_current_file_metadata(
        self,
        file_path: FileIndexPath,
    ) -> CurrentFileMetadata | None:
        """Return current storage metadata for one file, or None when missing."""


@dataclass(frozen=True, slots=True)
class RepositoryIndexedFileChecksumSource:
    """Load indexed file checksums from the entity repository."""

    session_maker: async_sessionmaker[AsyncSession]
    entity_repository: IndexedFileChecksumRepository

    async def load_indexed_file_checksums(
        self,
        file_paths: Sequence[FileIndexPath],
    ) -> Mapping[FileIndexPath, FileIndexChecksum | None]:
        """Load accepted entity checksums for target paths."""
        async with self.session_maker() as session:
            rows = await self.entity_repository.get_by_file_paths(session, file_paths)
        return {str(row[0]): None if row[1] is None else str(row[1]) for row in rows}


@dataclass(frozen=True, slots=True)
class StorageCurrentFileChecksumSource:
    """Load current file checksums from storage metadata."""

    metadata_source: CurrentFileMetadataSource

    async def load_current_file_checksum(
        self,
        file_path: FileIndexPath,
    ) -> FileIndexChecksum | None:
        """Return the current storage checksum for one file."""
        try:
            current_metadata = await self.metadata_source.load_current_file_metadata(file_path)
        except FileOperationError:
            return None
        return current_metadata.checksum if current_metadata is not None else None


@dataclass(frozen=True, slots=True)
class FileIndexChecker:
    """Plan content reads using indexed and current file checksums."""

    indexed_checksum_source: IndexedFileChecksumSource
    current_checksum_source: CurrentFileChecksumSource

    async def detect(self, targets: Sequence[FileIndexTarget]) -> FileIndexPlan:
        """Return the file paths whose current storage object still needs indexing."""
        if not targets:
            return FileIndexPlan(paths_to_read=(), decisions=())

        if not any(target.observed_checksum is not None for target in targets):
            return plan_legacy_file_index_targets(targets)

        indexed_checksum_by_path = await self.indexed_checksum_source.load_indexed_file_checksums(
            tuple(target.path for target in targets)
        )
        decisions: list[FileIndexDecision] = []
        for target in targets:
            decision = await self.inspect_target(
                target,
                indexed_checksum=indexed_checksum_by_path.get(target.path),
            )
            decisions.append(decision)

        return build_file_index_plan(decisions)

    async def inspect_target(
        self,
        target: FileIndexTarget,
        *,
        indexed_checksum: FileIndexChecksum | None,
    ) -> FileIndexDecision:
        """Inspect one file target without reading its content."""
        observed_decision = plan_file_index_target_from_observed(
            target,
            db_checksum=indexed_checksum,
        )
        if observed_decision is not None:
            return observed_decision

        current_checksum = await self.current_checksum_source.load_current_file_checksum(
            target.path
        )
        return plan_file_index_target_from_current(
            target,
            db_checksum=indexed_checksum,
            current_checksum=current_checksum,
        )
