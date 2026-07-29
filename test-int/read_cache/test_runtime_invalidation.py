"""Real Redis coverage for non-request cache invalidation boundaries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast, override

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.index import note_content_materialization
from basic_memory.index.local_moves import (
    LocalMoveEntityRepository,
    LocalWatchMoveProcessor,
)
from basic_memory.index.local_dependencies import LocalIndexSearchService
from basic_memory.index.local_project import LocalProjectIndexRuntime, run_local_project_index
from basic_memory.index.local_runtime import LocalInlineStorageEventResultRecorder
from basic_memory.indexing.change_planning import ChangeReport
from basic_memory.indexing.directory_delete_runner import (
    DirectoryDeleteRuntime,
    RepositoryDirectoryDeleteAcceptanceStore,
)
from basic_memory.indexing.project_index_maintenance import (
    ProjectIndexDeleteRun,
    ProjectIndexMoveRun,
    ProjectIndexMovedEntitySearchRefresher,
)
from basic_memory.indexing.relation_resolution import RelationResolutionRuntime
from basic_memory.models import Project
from basic_memory.models.knowledge import Entity
from basic_memory.read_cache import (
    ReadCacheKey,
    ReadCacheOperation,
    read_cache_request_digest,
)
from basic_memory.read_cache.keys import redis_read_cache_generation_key
from basic_memory.read_cache.redis import RedisReadCache
from basic_memory.repository import EntityRepository
from basic_memory.repository.note_content_repository import (
    AcceptedNoteContentWrite,
    NoteContentRepository,
)
from basic_memory.runtime.cleanup import RuntimeFileDeleteResult, RuntimeNoteFileDeleteJobRequest
from basic_memory.runtime.jobs import (
    RuntimeIndexFileBatchJobRequest,
    RuntimeObservedIndexFile,
    RuntimeProjectIndexJobRequest,
)
from basic_memory.runtime.projects import ProjectRuntimeReference
from basic_memory.runtime.storage import (
    STORAGE_OBJECT_DELETED_EVENT,
    StorageEventPayload,
    StorageObjectIdentity,
    StorageObjectVersion,
    RuntimeStorageEventOperation,
    RuntimeStorageEventOperationKind,
)
from basic_memory.services.directory_deletes import DirectoryDeleteService
from basic_memory.services.file_service import FileService
from basic_memory.services.initialization import recover_project_materializations

pytestmark = pytest.mark.redis


class RedisCacheHarness(Protocol):
    cache: RedisReadCache
    client: Redis
    namespace: str
    prefix: str


class DetectedMoveProcessor(LocalWatchMoveProcessor):
    """Exercise move completion without coupling the test to detection I/O."""

    @override
    async def detect_moves(
        self,
        events: Sequence[StorageEventPayload],
    ) -> tuple[dict[str, str], set[int]]:
        del events
        return {"notes/old.md": "notes/new.md"}, {0, 1}

    @override
    async def detect_transient_missing_events(
        self,
        events: Sequence[StorageEventPayload],
        *,
        exclude_indexes: set[int],
    ) -> set[int]:
        del events, exclude_indexes
        return set()

    @override
    async def detect_missing_entity_delete_events(
        self,
        events: Sequence[StorageEventPayload],
        *,
        exclude_indexes: set[int],
    ) -> set[int]:
        del events, exclude_indexes
        return set()


class RecordingMoveMaintenance:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, str], int]] = []

    async def run_move_batches(
        self,
        *,
        moved_files: Mapping[str, str],
        batch_size: int,
    ) -> ProjectIndexMoveRun:
        self.calls.append((dict(moved_files), batch_size))
        return ProjectIndexMoveRun(
            total_moves=1,
            total_updated_files=1,
            records=(),
            moved_entity_ids=frozenset({17}),
        )

    async def run_delete_batches(
        self,
        *,
        deleted_paths: Sequence[str],
        batch_size: int,
    ) -> ProjectIndexDeleteRun:
        del deleted_paths, batch_size
        raise AssertionError("watcher move completion must not run delete maintenance")


class RecordingMovedEntitySearchRefresher:
    def __init__(self) -> None:
        self.calls: list[list[int]] = []

    async def refresh_moved_entities(self, entity_ids: Sequence[int]) -> None:
        self.calls.append(list(entity_ids))


class EmptyObservedFileSource:
    async def list_observed_index_files(self) -> tuple[RuntimeObservedIndexFile, ...]:
        return ()


class DeletedFileChangeDetector:
    async def detect_all_changes(
        self,
        storage_files: Mapping[str, RuntimeObservedIndexFile],
    ) -> ChangeReport:
        del storage_files
        return ChangeReport(deleted_files=["notes/stale.md"])


class FailingDeleteMaintenance:
    async def run_move_batches(
        self,
        *,
        moved_files: Mapping[str, str],
        batch_size: int,
    ) -> ProjectIndexMoveRun:
        del moved_files, batch_size
        return ProjectIndexMoveRun(
            total_moves=0,
            total_updated_files=0,
            records=(),
        )

    async def run_delete_batches(
        self,
        *,
        deleted_paths: Sequence[str],
        batch_size: int,
    ) -> ProjectIndexDeleteRun:
        del deleted_paths, batch_size
        raise RuntimeError("partial project index failure")


class UnusedBatchEnqueuer:
    async def enqueue_index_file_batch(
        self,
        request: RuntimeIndexFileBatchJobRequest,
    ) -> None:
        del request
        raise AssertionError("failing maintenance must stop before file batches")


class GenerationObservingDirectoryDeleteEnqueuer:
    def __init__(
        self,
        redis_cache: RedisCacheHarness,
        project_external_id: str,
    ) -> None:
        self.redis_cache = redis_cache
        self.project_external_id = project_external_id
        self.observed_generations: list[bytes | str] = []

    async def enqueue_directory_file_delete(
        self,
        request: RuntimeNoteFileDeleteJobRequest,
    ) -> RuntimeFileDeleteResult:
        generation = await self.redis_cache.client.get(
            redis_read_cache_generation_key(
                prefix=self.redis_cache.prefix,
                namespace=self.redis_cache.namespace,
                project_id=self.project_external_id,
            )
        )
        assert generation is not None
        self.observed_generations.append(generation)
        return RuntimeFileDeleteResult.already_absent(
            entity_id=request.entity_id,
            file_path=request.file_path,
        )


async def _initialized_generation(
    redis_cache: RedisCacheHarness,
    project_external_id: str,
    *,
    request: str,
) -> bytes | str:
    await redis_cache.cache.lookup(
        ReadCacheKey(
            project_id=project_external_id,
            operation=ReadCacheOperation.entity,
            request_digest=read_cache_request_digest(request),
        )
    )
    generation = await redis_cache.client.get(
        redis_read_cache_generation_key(
            prefix=redis_cache.prefix,
            namespace=redis_cache.namespace,
            project_id=project_external_id,
        )
    )
    assert generation is not None
    return generation


async def _seed_recovery_note(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
    *,
    title: str,
    file_path: str,
    markdown_content: str,
) -> Entity:
    entity_repository = EntityRepository(project_id=project.id)
    content_repository = NoteContentRepository(project_id=project.id)
    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.add(
            session,
            Entity(
                title=title,
                note_type="note",
                content_type="text/markdown",
                file_path=file_path,
                checksum="entity-checksum-1",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
        )
        await content_repository.accept_write(
            session,
            AcceptedNoteContentWrite(
                entity_id=entity.id,
                markdown_content=markdown_content,
                db_version=1,
                db_checksum="db-checksum-1",
                last_source="api",
                updated_at=datetime.now(UTC),
            ),
        )
        row = await content_repository.select_by_id(session, entity.id)
        assert row is not None
        row.file_write_status = "writing"
        await session.flush()
    return entity


def _move_event(event_name: str, path: str) -> StorageEventPayload:
    return StorageEventPayload(
        event_name=event_name,
        event_time="2026-07-29T00:00:00Z",
        object_version=StorageObjectVersion(
            identity=StorageObjectIdentity(
                bucket_name="local-filesystem",
                key=f"project/{path}",
            ),
            etag="move-etag",
        ),
    )


def _index_operation(path: str) -> RuntimeStorageEventOperation:
    return RuntimeStorageEventOperation(
        kind=RuntimeStorageEventOperationKind.index_file,
        storage_event=_move_event("OBJECT_CREATED_PUT", path),
        relative_path=path,
    )


@pytest.mark.asyncio
async def test_watcher_move_completion_invalidates_real_redis(
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    project_external_id = str(test_project.external_id)
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="watcher-move",
    )
    maintenance = RecordingMoveMaintenance()
    search_refresher = RecordingMovedEntitySearchRefresher()
    processor = DetectedMoveProcessor(
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
        file_service=cast(FileService, object()),
        entity_repository=cast(LocalMoveEntityRepository, object()),
        maintenance_runner=maintenance,
        moved_entity_search_refresher=search_refresher,
        project_external_id=project_external_id,
        read_cache=redis_cache.cache,
    )

    result = await processor.process_moves(
        (
            _move_event(STORAGE_OBJECT_DELETED_EVENT, "notes/old.md"),
            _move_event("OBJECT_CREATED_PUT", "notes/new.md"),
        )
    )

    assert result.remaining_events == ()
    assert result.processed_moves == 1
    assert maintenance.calls == [({"notes/old.md": "notes/new.md"}, 100)]
    assert search_refresher.calls == [[17]]
    generation_after = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="watcher-move",
    )
    assert generation_after != generation_before


@pytest.mark.asyncio
async def test_watcher_index_failure_invalidates_real_redis(
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    project_external_id = str(test_project.external_id)
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="watcher-index-failure",
    )
    recorder = LocalInlineStorageEventResultRecorder(
        project=ProjectRuntimeReference.from_project(test_project),
        search_service=cast(LocalIndexSearchService, object()),
        relation_cleanup_search_refresher=cast(
            ProjectIndexMovedEntitySearchRefresher,
            object(),
        ),
        relation_runtime=cast(RelationResolutionRuntime, object()),
        index_embeddings=False,
        read_cache=redis_cache.cache,
    )

    await recorder.event_failed(
        _index_operation("notes/partial-index.md"),
        RuntimeError("partial watcher index failure"),
    )

    generation_after = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="watcher-index-failure",
    )
    assert generation_after != generation_before


@pytest.mark.asyncio
async def test_startup_materialization_recovery_invalidates_real_redis(
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    _, session_maker = engine_factory
    project_external_id = str(test_project.external_id)
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="startup-recovery",
    )
    entity = await _seed_recovery_note(
        session_maker,
        test_project,
        title="Recovered Note",
        file_path="notes/recovered.md",
        markdown_content="# Recovered with cache invalidation\n",
    )

    await recover_project_materializations(
        test_project,
        session_maker,
        read_cache=redis_cache.cache,
    )

    written = Path(test_project.path) / entity.file_path
    assert written.read_text(encoding="utf-8") == "# Recovered with cache invalidation\n"
    generation_after = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="startup-recovery",
    )
    assert generation_after != generation_before


@pytest.mark.asyncio
async def test_startup_recovery_invalidates_before_later_vacate_failure_in_real_redis(
    monkeypatch: pytest.MonkeyPatch,
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """A later recovery phase failure cannot retain phase-one published state."""
    _, session_maker = engine_factory
    project_external_id = str(test_project.external_id)
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="startup-recovery-later-failure",
    )
    entity = await _seed_recovery_note(
        session_maker,
        test_project,
        title="Recovered Before Vacate Failure",
        file_path="notes/recovered-before-vacate-failure.md",
        markdown_content="# Recovered before later failure\n",
    )

    async def fail_move_vacate_recovery(
        *,
        session_maker: async_sessionmaker[AsyncSession],
        file_service: FileService,
        project_id: int,
    ) -> int:
        del session_maker, file_service, project_id
        raise RuntimeError("move-vacate setup failure")

    monkeypatch.setattr(
        note_content_materialization,
        "recover_move_vacates",
        fail_move_vacate_recovery,
    )

    await recover_project_materializations(
        test_project,
        session_maker,
        read_cache=redis_cache.cache,
    )

    written = Path(test_project.path) / entity.file_path
    assert written.read_text(encoding="utf-8") == "# Recovered before later failure\n"
    generation_after = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="startup-recovery-later-failure",
    )
    assert generation_after != generation_before


@pytest.mark.asyncio
async def test_startup_recovery_conflict_invalidates_published_failure_in_real_redis(
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    _, session_maker = engine_factory
    project_external_id = str(test_project.external_id)
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="startup-recovery-conflict",
    )
    entity = await _seed_recovery_note(
        session_maker,
        test_project,
        title="Conflicted Recovery",
        file_path="notes/conflicted-recovery.md",
        markdown_content="# Accepted recovery content\n",
    )
    target = Path(test_project.path) / entity.file_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# External edit\n", encoding="utf-8")

    await recover_project_materializations(
        test_project,
        session_maker,
        read_cache=redis_cache.cache,
    )

    content_repository = NoteContentRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        row = await content_repository.get_by_entity_id(session, entity.id)
    assert row is not None
    assert row.file_write_status == "external_change_detected"
    generation_after = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="startup-recovery-conflict",
    )
    assert generation_after != generation_before


@pytest.mark.asyncio
async def test_project_index_failure_invalidates_real_redis(
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    project_external_id = str(test_project.external_id)
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="project-index-failure",
    )

    with pytest.raises(RuntimeError, match="partial project index failure"):
        await run_local_project_index(
            RuntimeProjectIndexJobRequest(
                project=ProjectRuntimeReference.from_project(test_project),
                embeddings=False,
            ),
            runtime=LocalProjectIndexRuntime(
                observed_file_source=EmptyObservedFileSource(),
                change_detector=DeletedFileChangeDetector(),
                maintenance_runner=FailingDeleteMaintenance(),
                moved_entity_search_refresher=RecordingMovedEntitySearchRefresher(),
                batch_enqueuer=UnusedBatchEnqueuer(),
                read_cache=redis_cache.cache,
            ),
        )

    generation_after = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="project-index-failure",
    )
    assert generation_after != generation_before


@pytest.mark.asyncio
async def test_directory_delete_invalidates_before_and_after_cleanup_in_real_redis(
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    _, session_maker = engine_factory
    project_external_id = str(test_project.external_id)
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="directory-delete",
    )
    entity_repository = EntityRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        await entity_repository.add(
            session,
            Entity(
                title="Deleted During Cleanup",
                note_type="note",
                content_type="text/markdown",
                file_path="delete-with-cache/note.md",
                checksum="directory-delete-checksum",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
        )

    enqueuer = GenerationObservingDirectoryDeleteEnqueuer(
        redis_cache,
        project_external_id,
    )
    service = DirectoryDeleteService(
        session_maker=session_maker,
        runtime=DirectoryDeleteRuntime(
            store=RepositoryDirectoryDeleteAcceptanceStore(),
            file_delete_enqueuer=enqueuer,
        ),
    )

    result = await service.delete_directory(
        project_external_id=project_external_id,
        directory="delete-with-cache",
        read_cache=redis_cache.cache,
    )

    assert result.deleted_files == ("delete-with-cache/note.md",)
    assert len(enqueuer.observed_generations) == 1
    generation_during_cleanup = enqueuer.observed_generations[0]
    assert generation_during_cleanup != generation_before
    generation_after = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="directory-delete",
    )
    assert generation_after != generation_during_cleanup
