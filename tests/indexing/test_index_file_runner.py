"""Tests for portable single-file index orchestration."""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import basic_memory.indexing.index_file_runner as index_file_runner_module
from basic_memory.indexing.file_index_planning import (
    FileIndexDecision,
    FileIndexDecisionStatus,
    FileIndexPlan,
    FileIndexTarget,
)
from basic_memory.indexing.index_file_runner import (
    IndexFileCurrentMetadataSource,
    IndexFileObjectMetadata,
    RepositoryCurrentMaterializedNoteSource,
    StorageIndexFileMetadataSource,
    run_index_file,
)
from basic_memory.indexing.index_file_runtime import IndexFileRuntimeRequest
from basic_memory.indexing.models import (
    CurrentMaterializedNoteEntity,
    FileIndexOperation,
    FileIndexResult,
    IndexFileJobResult,
    IndexFileJobStatus,
)
from basic_memory.runtime import (
    NOTE_OBJECT_ACTOR_USER_PROFILE_ID_METADATA,
    NOTE_OBJECT_DB_VERSION_METADATA,
    NOTE_OBJECT_FILE_CHECKSUM_METADATA,
    NOTE_OBJECT_SOURCE_METADATA,
    RuntimeStorageFileIndexMode,
    RuntimeStorageObjectObservation,
)
from basic_memory.services.exceptions import FileOperationError


class FakeChecker:
    def __init__(self, plan: FileIndexPlan) -> None:
        self.plan = plan
        self.targets: tuple[FileIndexTarget, ...] | None = None

    async def detect(self, targets: Sequence[FileIndexTarget]) -> FileIndexPlan:
        self.targets = tuple(targets)
        return self.plan


class FakeMetadataSource:
    def __init__(
        self,
        metadata: IndexFileObjectMetadata | None,
        *,
        error: FileOperationError | None = None,
    ) -> None:
        self.metadata = metadata
        self.error = error
        self.paths: list[str] = []

    async def load_current_file_metadata(self, file_path: str) -> IndexFileObjectMetadata | None:
        self.paths.append(file_path)
        if self.error is not None:
            raise self.error
        return self.metadata


@dataclass(frozen=True, slots=True)
class FakeStorageMetadata:
    checksum: str
    metadata: dict[str, str]


@dataclass(slots=True)
class FakeStorageMetadataSource:
    calls: list[str]

    async def load_current_file_metadata(
        self,
        file_path: str,
    ) -> FakeStorageMetadata | None:
        self.calls.append(file_path)
        return FakeStorageMetadata(
            checksum="etag-1",
            metadata={NOTE_OBJECT_SOURCE_METADATA: "api"},
        )


class FakeMaterializedNoteSource:
    def __init__(self, entity: CurrentMaterializedNoteEntity | None) -> None:
        self.entity = entity
        self.paths: list[str] = []

    async def load_current_materialized_note_entity(
        self,
        file_path: str,
    ) -> CurrentMaterializedNoteEntity | None:
        self.paths.append(file_path)
        return self.entity


class FakeRepositoryEntity:
    id = 42
    external_id = "note-42"
    title = "Repository Note"
    permalink = "notes/repository-note"
    checksum = "checksum-1"


class FakeEntityRepository:
    def __init__(self, entity: FakeRepositoryEntity | None) -> None:
        self.entity = entity
        self.calls: list[tuple[AsyncSession, str, bool]] = []

    async def get_by_file_path(
        self,
        session: AsyncSession,
        file_path: str,
        *,
        load_relations: bool = True,
    ) -> FakeRepositoryEntity | None:
        self.calls.append((session, file_path, load_relations))
        return self.entity


class FakeFileIndexer:
    def __init__(
        self,
        indexed_file: FileIndexResult | None = None,
        *,
        error: FileOperationError | None = None,
    ) -> None:
        self.indexed_file = indexed_file
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def index_file(self, file_path: str, *, source: str) -> FileIndexResult:
        self.calls.append((file_path, source))
        if self.error is not None:
            raise self.error
        if self.indexed_file is None:
            raise AssertionError("indexed_file is required when no error is configured")
        return self.indexed_file


def observed_request() -> IndexFileRuntimeRequest:
    return IndexFileRuntimeRequest(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_path="main",
        file_path="notes/a.md",
        mode=RuntimeStorageFileIndexMode.observed_object,
        object_observation=RuntimeStorageObjectObservation(etag="etag-1", size=12),
    )


def current_file_request() -> IndexFileRuntimeRequest:
    return IndexFileRuntimeRequest(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        project_id=101,
        project_external_id="project-main",
        project_name="Main",
        project_path="main",
        file_path="notes/a.md",
        mode=RuntimeStorageFileIndexMode.current_file,
    )


def current_plan() -> FileIndexPlan:
    return FileIndexPlan(
        paths_to_read=(),
        decisions=(
            FileIndexDecision(
                path="notes/a.md",
                status=FileIndexDecisionStatus.current,
                reason="file already indexed: notes/a.md",
            ),
        ),
    )


def read_plan() -> FileIndexPlan:
    return FileIndexPlan(paths_to_read=("notes/a.md",), decisions=())


def indexed_file() -> FileIndexResult:
    return FileIndexResult(
        file_path="notes/a.md",
        entity_id=42,
        external_id="note-42",
        title="Test Note",
        permalink="notes/test-note",
        checksum="checksum-1",
        operation=FileIndexOperation.updated,
    )


@pytest.mark.asyncio
async def test_storage_index_file_metadata_source_maps_storage_metadata() -> None:
    calls: list[str] = []
    metadata_source: IndexFileCurrentMetadataSource = FakeStorageMetadataSource(calls)
    source = StorageIndexFileMetadataSource(metadata_source=metadata_source)

    result = await source.load_current_file_metadata("notes/a.md")

    assert result == IndexFileObjectMetadata(
        checksum="etag-1",
        metadata={NOTE_OBJECT_SOURCE_METADATA: "api"},
    )
    assert calls == ["notes/a.md"]


@pytest.mark.asyncio
async def test_repository_current_materialized_note_source_loads_entity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeEntityRepository(FakeRepositoryEntity())
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    sessions: list[AsyncSession] = []

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[AsyncSession]:
        assert scoped_session_maker is session_maker
        session = cast(AsyncSession, object())
        sessions.append(session)
        yield session

    monkeypatch.setattr(index_file_runner_module.db, "scoped_session", fake_scoped_session)

    source = RepositoryCurrentMaterializedNoteSource(
        session_maker=session_maker,
        entity_repository=repository,
    )

    result = await source.load_current_materialized_note_entity("notes/a.md")

    assert result == CurrentMaterializedNoteEntity(
        entity_id=42,
        external_id="note-42",
        title="Repository Note",
        permalink="notes/repository-note",
        checksum="checksum-1",
    )
    assert repository.calls == [(sessions[0], "notes/a.md", False)]


@pytest.mark.asyncio
async def test_repository_current_materialized_note_source_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeEntityRepository(None)
    session_maker = cast(async_sessionmaker[AsyncSession], object())

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[AsyncSession]:
        assert scoped_session_maker is session_maker
        yield cast(AsyncSession, object())

    monkeypatch.setattr(index_file_runner_module.db, "scoped_session", fake_scoped_session)

    source = RepositoryCurrentMaterializedNoteSource(
        session_maker=session_maker,
        entity_repository=repository,
    )

    result = await source.load_current_materialized_note_entity("notes/missing.md")

    assert result is None


@pytest.mark.asyncio
async def test_run_index_file_preserves_current_materialized_note_metadata() -> None:
    metadata_source = FakeMetadataSource(
        IndexFileObjectMetadata(
            checksum="storage-native-etag",
            metadata={
                NOTE_OBJECT_FILE_CHECKSUM_METADATA: "checksum-1",
                NOTE_OBJECT_ACTOR_USER_PROFILE_ID_METADATA: (
                    "33333333-3333-3333-3333-333333333333"
                ),
                NOTE_OBJECT_SOURCE_METADATA: "mcp",
                NOTE_OBJECT_DB_VERSION_METADATA: "1",
            },
        )
    )
    materialized_source = FakeMaterializedNoteSource(
        CurrentMaterializedNoteEntity(
            entity_id=42,
            external_id="note-42",
            title="Created through MCP",
            permalink="notes/created-through-mcp",
            checksum="checksum-1",
        )
    )
    file_indexer = FakeFileIndexer()

    result = await run_index_file(
        observed_request(),
        checker=FakeChecker(current_plan()),
        metadata_source=metadata_source,
        materialized_note_source=materialized_source,
        file_indexer=file_indexer,
    )

    assert result == IndexFileJobResult(
        status=IndexFileJobStatus.current,
        reason="file already indexed: notes/a.md",
        entity_id=42,
        note_external_id="note-42",
        title="Created through MCP",
        permalink="notes/created-through-mcp",
        entity_checksum="checksum-1",
        operation=FileIndexOperation.created,
        actor_user_profile_id="33333333-3333-3333-3333-333333333333",
        live_update_source="mcp",
    )
    assert metadata_source.paths == ["notes/a.md"]
    assert materialized_source.paths == ["notes/a.md"]
    assert file_indexer.calls == []


@pytest.mark.asyncio
async def test_run_index_file_indexes_observed_object_and_uses_live_update_metadata() -> None:
    metadata_source = FakeMetadataSource(
        IndexFileObjectMetadata(
            checksum="storage-native-etag",
            metadata={
                NOTE_OBJECT_FILE_CHECKSUM_METADATA: "checksum-1",
                NOTE_OBJECT_ACTOR_USER_PROFILE_ID_METADATA: (
                    "33333333-3333-3333-3333-333333333333"
                ),
                NOTE_OBJECT_SOURCE_METADATA: "api",
            },
        )
    )

    result = await run_index_file(
        observed_request(),
        checker=FakeChecker(read_plan()),
        metadata_source=metadata_source,
        materialized_note_source=FakeMaterializedNoteSource(None),
        file_indexer=FakeFileIndexer(indexed_file()),
    )

    assert result.status == IndexFileJobStatus.processed
    assert result.entity_id == 42
    assert result.actor_user_profile_id == "33333333-3333-3333-3333-333333333333"
    assert result.live_update_source == "api"
    assert metadata_source.paths == ["notes/a.md"]


@pytest.mark.asyncio
async def test_run_index_file_current_file_mode_skips_missing_file() -> None:
    file_indexer = FakeFileIndexer()

    result = await run_index_file(
        current_file_request(),
        checker=FakeChecker(read_plan()),
        metadata_source=FakeMetadataSource(None),
        materialized_note_source=FakeMaterializedNoteSource(None),
        file_indexer=file_indexer,
    )

    assert result == IndexFileJobResult(
        status=IndexFileJobStatus.missing,
        reason="file not found: notes/a.md",
    )
    assert file_indexer.calls == []


@pytest.mark.asyncio
async def test_run_index_file_treats_delete_after_metadata_check_as_missing() -> None:
    result = await run_index_file(
        observed_request(),
        checker=FakeChecker(read_plan()),
        metadata_source=FakeMetadataSource(None),
        materialized_note_source=FakeMaterializedNoteSource(None),
        file_indexer=FakeFileIndexer(error=FileOperationError("file disappeared")),
    )

    assert result == IndexFileJobResult(
        status=IndexFileJobStatus.missing,
        reason="file deleted before indexing: notes/a.md",
    )
