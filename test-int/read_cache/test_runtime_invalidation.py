"""Real Redis coverage for non-request cache invalidation boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol, cast, override

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import basic_memory.indexing.external_file_delete_runner as external_file_delete_runner
import basic_memory.indexing.index_file_runner as index_file_runner
import basic_memory.services.directory_deletes as directory_deletes
import basic_memory.services.entity_service as entity_service_module
from basic_memory import db
from basic_memory.config import ProjectConfig, BasicMemoryConfig
from basic_memory.index import note_content_materialization
from basic_memory.index.local_moves import (
    LocalMoveEntityRepository,
    LocalWatchMoveProcessor,
)
from basic_memory.index.local_dependencies import LocalIndexSearchService
from basic_memory.index.local_schedulers import (
    LocalSearchReindexScheduler,
    drain_background_tasks,
)
from basic_memory.index.local_project import (
    LocalProjectIndexBatchEnqueuer,
    LocalProjectIndexRuntime,
    run_local_project_index,
)
from basic_memory.index.local_runtime import LocalInlineStorageEventResultRecorder
from basic_memory.indexing.change_planning import ChangeReport
from basic_memory.indexing.directory_delete_runner import (
    DirectoryDeleteRuntime,
    RepositoryDirectoryDeleteAcceptanceStore,
)
from basic_memory.indexing.file_batch_runner import (
    IndexFileBatchChecker,
    IndexFileBatchContentClassifier,
    IndexFileBatchIndexer,
    IndexFileBatchReader,
)
from basic_memory.indexing.external_file_delete_runner import (
    ExternalFileDeleteResult,
    InvalidatingExternalFileDeleteEntities,
    RepositoryExternalFileDeleteEntities,
)
from basic_memory.indexing.models import IndexFileJobResult, IndexFileJobStatus, IndexInputFile
from basic_memory.indexing.models import FileIndexResult
from basic_memory.indexing.index_file_runner import InvalidatingIndexFileExecutor
from basic_memory.indexing.project_index_maintenance import (
    InvalidatingProjectIndexBatchStore,
    ProjectIndexDeleteBatch,
    ProjectIndexDeleteBatchResult,
    ProjectIndexDeleteRun,
    ProjectIndexMoveBatch,
    ProjectIndexMoveBatchResult,
    ProjectIndexMoveRun,
    ProjectIndexMovedEntitySearchRefresher,
    StoreProjectIndexMaintenanceRunner,
)
from basic_memory.indexing.relation_resolution import RelationResolutionRuntime
from basic_memory.markdown import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.models import Project
from basic_memory.models.knowledge import Entity
from basic_memory.read_cache import (
    ReadCacheInvalidationStatus,
    ReadCacheKey,
    ReadCacheOperation,
    read_cache_request_digest,
)
from basic_memory.read_cache.keys import redis_read_cache_generation_key
from basic_memory.read_cache.redis import RedisReadCache
from basic_memory.repository import (
    EntityRepository,
    ObservationRepository,
    RelationRepository,
)
from basic_memory.repository.note_content_repository import (
    AcceptedNoteContentWrite,
    NoteContentRepository,
)
from basic_memory.repository.note_file_vacate_repository import NoteFileVacateRepository
from basic_memory.runtime.cleanup import (
    RuntimeExternalFileDeletePlan,
    RuntimeFileDeleteResult,
    RuntimeNoteFileDeleteJobRequest,
)
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
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.services.directory_deletes import DirectoryDeleteService
from basic_memory.services.entity_service import EntityService
from basic_memory.services.file_service import FileService
from basic_memory.services.initialization import recover_project_materializations
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.services.search_service import SearchService

pytestmark = pytest.mark.redis


class RedisCacheHarness(Protocol):
    cache: RedisReadCache
    client: Redis
    namespace: str
    prefix: str


async def _current_generation(
    redis_cache: RedisCacheHarness,
    project_external_id: str,
) -> bytes | str:
    generation = await redis_cache.client.get(
        redis_read_cache_generation_key(
            prefix=redis_cache.prefix,
            namespace=redis_cache.namespace,
            project_id=project_external_id,
        )
    )
    assert generation is not None
    return generation


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


class DetectedMoveBatchProcessor(DetectedMoveProcessor):
    """Exercise multiple durable watcher move batches without detection I/O."""

    @override
    async def detect_moves(
        self,
        events: Sequence[StorageEventPayload],
    ) -> tuple[dict[str, str], set[int]]:
        del events
        return {
            "notes/old-1.md": "notes/new-1.md",
            "notes/old-2.md": "notes/new-2.md",
        }, {0, 1, 2, 3}


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


class GenerationObservingProjectIndexBatchStore:
    """Record the real Redis generation seen before each durable batch."""

    def __init__(
        self,
        redis_cache: RedisCacheHarness,
        project_external_id: str,
    ) -> None:
        self.redis_cache = redis_cache
        self.project_external_id = project_external_id
        self.move_generations: list[bytes | str] = []
        self.delete_generations: list[bytes | str] = []

    async def apply_project_index_move_batch(
        self,
        move_batch: ProjectIndexMoveBatch,
    ) -> ProjectIndexMoveBatchResult:
        self.move_generations.append(
            await _current_generation(self.redis_cache, self.project_external_id)
        )
        return ProjectIndexMoveBatchResult(updated_files=len(move_batch.targets))

    async def apply_project_index_delete_batch(
        self,
        delete_batch: ProjectIndexDeleteBatch,
    ) -> ProjectIndexDeleteBatchResult:
        self.delete_generations.append(
            await _current_generation(self.redis_cache, self.project_external_id)
        )
        return ProjectIndexDeleteBatchResult(deleted_entities=len(delete_batch.paths))


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


class BlockingInvalidationRedisReadCache(RedisReadCache):
    """Hold one real invalidation so cancellation can repeat during cleanup."""

    def __init__(
        self,
        *,
        client: Redis,
        namespace: str,
        prefix: str,
        block_on_call: int = 1,
    ) -> None:
        super().__init__(client=client, namespace=namespace, prefix=prefix)
        self.block_on_call = block_on_call
        self.invalidation_calls = 0
        self.invalidation_started = asyncio.Event()
        self.release_invalidation = asyncio.Event()

    @override
    async def invalidate_project(self, project_id: str) -> ReadCacheInvalidationStatus:
        self.invalidation_calls += 1
        if self.invalidation_calls == self.block_on_call:
            self.invalidation_started.set()
            await self.release_invalidation.wait()
        return await super().invalidate_project(project_id)


class PartiallyFailingSearchReindexService:
    """Observe the generation inside a rebuild that fails after publishing work."""

    def __init__(
        self,
        redis_cache: RedisCacheHarness,
        project_external_id: str,
    ) -> None:
        self.redis_cache = redis_cache
        self.project_external_id = project_external_id
        self.generation_during_reindex: bytes | str | None = None

    async def reindex_all(self) -> None:
        self.generation_during_reindex = await _current_generation(
            self.redis_cache,
            self.project_external_id,
        )
        raise RuntimeError("partial search reindex failure")


class CommittingIndexFileExecutor:
    """Publish one real entity row before the caller cancels transaction exit."""

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        entity_repository: EntityRepository,
    ) -> None:
        self.session_maker = session_maker
        self.entity_repository = entity_repository
        self.entity_id: int | None = None

    async def index_file(self, file_path: str, *, source: str) -> FileIndexResult:
        del source
        async with db.scoped_session(self.session_maker) as session:
            entity = await self.entity_repository.add(
                session,
                Entity(
                    title="Cancelled Watcher Index Commit",
                    note_type="note",
                    content_type="text/markdown",
                    file_path=file_path,
                    checksum="cancelled-watcher-index-commit-checksum",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                ),
            )
            self.entity_id = entity.id
        raise AssertionError("test transaction-exit pause should be cancelled")


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
    return await _current_generation(redis_cache, project_external_id)


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
async def test_watcher_move_batches_invalidate_real_redis_before_next_batch(
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """Every committed watcher move batch advances Redis before the next batch."""
    project_external_id = str(test_project.external_id)
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="watcher-move-batches",
    )
    observed_store = GenerationObservingProjectIndexBatchStore(
        redis_cache,
        project_external_id,
    )
    invalidating_store = InvalidatingProjectIndexBatchStore(
        move_store=observed_store,
        delete_store=observed_store,
        read_cache=redis_cache.cache,
        project_external_id=project_external_id,
    )
    processor = DetectedMoveBatchProcessor(
        session_maker=cast(async_sessionmaker[AsyncSession], object()),
        file_service=cast(FileService, object()),
        entity_repository=cast(LocalMoveEntityRepository, object()),
        maintenance_runner=StoreProjectIndexMaintenanceRunner(
            move_store=invalidating_store,
            delete_store=invalidating_store,
        ),
        moved_entity_search_refresher=RecordingMovedEntitySearchRefresher(),
        project_external_id=project_external_id,
        read_cache=redis_cache.cache,
        batch_size=1,
    )

    result = await processor.process_moves(
        (
            _move_event(STORAGE_OBJECT_DELETED_EVENT, "notes/old-1.md"),
            _move_event("OBJECT_CREATED_PUT", "notes/new-1.md"),
            _move_event(STORAGE_OBJECT_DELETED_EVENT, "notes/old-2.md"),
            _move_event("OBJECT_CREATED_PUT", "notes/new-2.md"),
        )
    )

    assert result.remaining_events == ()
    assert result.processed_moves == 2
    assert observed_store.move_generations[0] == generation_before
    assert observed_store.move_generations[1] != observed_store.move_generations[0]
    generation_after = await _current_generation(redis_cache, project_external_id)
    assert generation_after != observed_store.move_generations[1]


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
async def test_partial_search_reindex_invalidates_real_redis(
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """A partial search rebuild cannot retain resolutions filled during the run."""
    project_external_id = str(test_project.external_id)
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="partial-search-reindex",
    )
    search_service = PartiallyFailingSearchReindexService(
        redis_cache,
        project_external_id,
    )
    scheduler = LocalSearchReindexScheduler(
        search_service=search_service,
        project_external_id=project_external_id,
        read_cache=redis_cache.cache,
        test_mode=False,
    )

    scheduler.schedule_search_reindex(project_id=test_project.id)
    await drain_background_tasks()

    assert search_service.generation_during_reindex == generation_before
    generation_after = await _current_generation(redis_cache, project_external_id)
    assert generation_after != generation_before


@pytest.mark.asyncio
@pytest.mark.parametrize("completion", ["index", "delete"])
async def test_cancelled_watcher_completion_finishes_real_redis_invalidation(
    test_project: Project,
    redis_cache: RedisCacheHarness,
    completion: Literal["index", "delete"],
) -> None:
    """Cancellation after a durable watcher event waits for invalidation."""
    project_external_id = str(test_project.external_id)
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request=f"cancelled-watcher-{completion}",
    )
    blocking_cache = BlockingInvalidationRedisReadCache(
        client=redis_cache.client,
        namespace=redis_cache.namespace,
        prefix=redis_cache.prefix,
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
        read_cache=blocking_cache,
    )

    if completion == "index":
        completion_call = recorder.index_file_completed(
            _index_operation("notes/cancelled-index.md"),
            IndexFileJobResult(
                status=IndexFileJobStatus.processed,
                reason="file indexed",
                entity_id=42,
            ),
        )
    else:
        deleted_entity = Entity(
            id=42,
            project_id=test_project.id,
            external_id="cancelled-watcher-delete",
            title="Cancelled watcher delete",
            permalink="notes/cancelled-watcher-delete",
            note_type="note",
            content_type="text/markdown",
            file_path="notes/cancelled-delete.md",
            checksum="cancelled-watcher-delete-checksum",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        completion_call = recorder.delete_file_completed(
            RuntimeStorageEventOperation(
                kind=RuntimeStorageEventOperationKind.delete_file,
                storage_event=_move_event(
                    STORAGE_OBJECT_DELETED_EVENT,
                    deleted_entity.file_path,
                ),
                relative_path=deleted_entity.file_path,
            ),
            ExternalFileDeleteResult(
                plan=RuntimeExternalFileDeletePlan.from_existing_entity(
                    deleted_entity,
                    file_path=deleted_entity.file_path,
                    object_exists=False,
                ),
                entity_deleted=True,
                deleted_entity=deleted_entity,
            ),
        )

    completion_task = asyncio.create_task(completion_call)
    async with asyncio.timeout(5):
        await blocking_cache.invalidation_started.wait()
    completion_task.cancel()
    await asyncio.sleep(0)
    completion_task.cancel()
    await asyncio.sleep(0)
    assert not completion_task.done()

    blocking_cache.release_invalidation.set()
    with pytest.raises(asyncio.CancelledError):
        await completion_task

    generation_after = await _initialized_generation(
        redis_cache,
        project_external_id,
        request=f"cancelled-watcher-{completion}",
    )
    assert generation_after != generation_before


@pytest.mark.asyncio
async def test_cancelled_watcher_delete_commit_finishes_real_redis_invalidation(
    monkeypatch: pytest.MonkeyPatch,
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """Cancellation during watcher delete transaction exit cannot skip invalidation."""
    _, session_maker = engine_factory
    project_external_id = str(test_project.external_id)
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="cancelled-watcher-delete-commit",
    )
    entity_repository = EntityRepository(project_id=test_project.id)
    file_path = "notes/cancelled-watcher-delete-commit.md"
    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.add(
            session,
            Entity(
                title="Cancelled Watcher Delete Commit",
                note_type="note",
                content_type="text/markdown",
                file_path=file_path,
                checksum="cancelled-watcher-delete-commit-checksum",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
        )

    blocking_cache = BlockingInvalidationRedisReadCache(
        client=redis_cache.client,
        namespace=redis_cache.namespace,
        prefix=redis_cache.prefix,
    )
    delete_entities = InvalidatingExternalFileDeleteEntities(
        entities=RepositoryExternalFileDeleteEntities(
            session_maker=session_maker,
            entity_repository=entity_repository,
        ),
        read_cache=blocking_cache,
        project_external_id=project_external_id,
    )
    found = await delete_entities.find_entity_by_file_path(file_path)
    assert found is not None
    assert found.id == entity.id

    transaction_committed = asyncio.Event()
    hold_after_commit = asyncio.Event()
    original_scoped_session = external_file_delete_runner.db.scoped_session

    @asynccontextmanager
    async def pause_after_delete_commit(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[AsyncSession]:
        async with original_scoped_session(scoped_session_maker) as session:
            yield session
        transaction_committed.set()
        await hold_after_commit.wait()

    monkeypatch.setattr(
        external_file_delete_runner.db,
        "scoped_session",
        pause_after_delete_commit,
    )
    delete_task = asyncio.create_task(
        delete_entities.delete_entity_if_file_path_matches(
            entity_id=entity.id,
            file_path=file_path,
        )
    )

    async with asyncio.timeout(5):
        await transaction_committed.wait()
    async with original_scoped_session(session_maker) as session:
        assert await entity_repository.get_by_id(session, entity.id) is None

    delete_task.cancel()
    async with asyncio.timeout(5):
        await blocking_cache.invalidation_started.wait()

    # Repeated cancellation must not abandon the in-flight Redis generation bump.
    delete_task.cancel()
    await asyncio.sleep(0)
    assert not delete_task.done()

    blocking_cache.release_invalidation.set()
    with pytest.raises(asyncio.CancelledError):
        await delete_task

    generation_after = await _current_generation(redis_cache, project_external_id)
    assert generation_after != generation_before


@pytest.mark.asyncio
async def test_cancelled_watcher_index_commit_finishes_real_redis_invalidation(
    monkeypatch: pytest.MonkeyPatch,
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """Cancellation during watcher index transaction exit cannot skip invalidation."""
    _, session_maker = engine_factory
    project_external_id = str(test_project.external_id)
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="cancelled-watcher-index-commit",
    )
    entity_repository = EntityRepository(project_id=test_project.id)
    executor = CommittingIndexFileExecutor(session_maker, entity_repository)
    blocking_cache = BlockingInvalidationRedisReadCache(
        client=redis_cache.client,
        namespace=redis_cache.namespace,
        prefix=redis_cache.prefix,
    )
    invalidating_executor = InvalidatingIndexFileExecutor(
        executor=executor,
        read_cache=blocking_cache,
        project_external_id=project_external_id,
    )

    transaction_committed = asyncio.Event()
    hold_after_commit = asyncio.Event()
    original_scoped_session = index_file_runner.db.scoped_session

    @asynccontextmanager
    async def pause_after_index_commit(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[AsyncSession]:
        async with original_scoped_session(scoped_session_maker) as session:
            yield session
        transaction_committed.set()
        await hold_after_commit.wait()

    monkeypatch.setattr(
        index_file_runner.db,
        "scoped_session",
        pause_after_index_commit,
    )
    file_path = "notes/cancelled-watcher-index-commit.md"
    index_task = asyncio.create_task(
        invalidating_executor.index_file(file_path, source="s3_webhook")
    )

    async with asyncio.timeout(5):
        await transaction_committed.wait()
    assert executor.entity_id is not None
    async with original_scoped_session(session_maker) as session:
        indexed_entity = await entity_repository.get_by_id(session, executor.entity_id)
    assert indexed_entity is not None
    assert indexed_entity.file_path == file_path

    index_task.cancel()
    async with asyncio.timeout(5):
        await blocking_cache.invalidation_started.wait()

    index_task.cancel()
    await asyncio.sleep(0)
    assert not index_task.done()

    blocking_cache.release_invalidation.set()
    with pytest.raises(asyncio.CancelledError):
        await index_task

    generation_after = await _current_generation(redis_cache, project_external_id)
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
async def test_cancelled_startup_materialization_recovery_finishes_real_redis_invalidation(
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """Cancellation after recovery commits waits for the Redis generation bump."""
    _, session_maker = engine_factory
    project_external_id = str(test_project.external_id)
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="cancelled-startup-materialization-recovery",
    )
    entity = await _seed_recovery_note(
        session_maker,
        test_project,
        title="Cancelled Startup Recovery",
        file_path="notes/cancelled-startup-recovery.md",
        markdown_content="# Recovered before cancellation\n",
    )
    blocking_cache = BlockingInvalidationRedisReadCache(
        client=redis_cache.client,
        namespace=redis_cache.namespace,
        prefix=redis_cache.prefix,
    )

    recovery_task = asyncio.create_task(
        recover_project_materializations(
            test_project,
            session_maker,
            read_cache=blocking_cache,
        )
    )
    async with asyncio.timeout(5):
        await blocking_cache.invalidation_started.wait()

    written = Path(test_project.path) / entity.file_path
    assert written.read_text(encoding="utf-8") == "# Recovered before cancellation\n"
    recovery_task.cancel()
    await asyncio.sleep(0)
    recovery_task.cancel()
    await asyncio.sleep(0)
    assert not recovery_task.done()

    blocking_cache.release_invalidation.set()
    with pytest.raises(asyncio.CancelledError):
        await recovery_task

    content_repository = NoteContentRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        row = await content_repository.get_by_entity_id(session, entity.id)
    assert row is not None
    assert row.file_write_status == "synced"
    generation_after = await _current_generation(redis_cache, project_external_id)
    assert generation_after != generation_before


@pytest.mark.asyncio
async def test_cancelled_startup_move_vacate_recovery_finishes_real_redis_invalidation(
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """Cancellation after vacate cleanup commits waits for its own generation bump."""
    _, session_maker = engine_factory
    markdown_content = "# Moved before cancellation\n"
    entity = await _seed_recovery_note(
        session_maker,
        test_project,
        title="Cancelled Startup Vacate Recovery",
        file_path="notes/cancelled-startup-vacate-recovery.md",
        markdown_content=markdown_content,
    )
    await recover_project_materializations(
        test_project,
        session_maker,
        read_cache=None,
    )

    source_relative = "old/cancelled-startup-vacate-recovery.md"
    source = Path(test_project.path) / source_relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(markdown_content, encoding="utf-8")
    source_checksum = sha256(markdown_content.encode()).hexdigest()
    vacate_repository = NoteFileVacateRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        await vacate_repository.record_vacate(
            session,
            entity_id=entity.id,
            file_path=source_relative,
            file_checksum=source_checksum,
        )

    project_external_id = str(test_project.external_id)
    await _initialized_generation(
        redis_cache,
        project_external_id,
        request="cancelled-startup-vacate-recovery",
    )
    blocking_cache = BlockingInvalidationRedisReadCache(
        client=redis_cache.client,
        namespace=redis_cache.namespace,
        prefix=redis_cache.prefix,
        block_on_call=2,
    )
    recovery_task = asyncio.create_task(
        recover_project_materializations(
            test_project,
            session_maker,
            read_cache=blocking_cache,
        )
    )
    async with asyncio.timeout(5):
        await blocking_cache.invalidation_started.wait()

    generation_before_vacate_invalidation = await _current_generation(
        redis_cache,
        project_external_id,
    )
    assert not source.exists()
    async with db.scoped_session(session_maker) as session:
        assert await vacate_repository.load_vacate_markers(session, [source_relative]) == {}

    recovery_task.cancel()
    await asyncio.sleep(0)
    recovery_task.cancel()
    await asyncio.sleep(0)
    assert not recovery_task.done()

    blocking_cache.release_invalidation.set()
    with pytest.raises(asyncio.CancelledError):
        await recovery_task

    generation_after = await _current_generation(redis_cache, project_external_id)
    assert generation_after != generation_before_vacate_invalidation


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
async def test_project_index_batches_invalidate_real_redis(
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """Every inline move, delete, and file batch advances the generation."""
    project_external_id = str(test_project.external_id)
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="project-index-batches",
    )
    observed_store = GenerationObservingProjectIndexBatchStore(
        redis_cache,
        project_external_id,
    )
    invalidating_store = InvalidatingProjectIndexBatchStore(
        move_store=observed_store,
        delete_store=observed_store,
        read_cache=redis_cache.cache,
        project_external_id=project_external_id,
    )
    maintenance = StoreProjectIndexMaintenanceRunner(
        move_store=invalidating_store,
        delete_store=invalidating_store,
    )

    await maintenance.run_move_batches(
        moved_files={
            "notes/old-1.md": "notes/new-1.md",
            "notes/old-2.md": "notes/new-2.md",
        },
        batch_size=1,
    )
    assert observed_store.move_generations[0] == generation_before
    assert observed_store.move_generations[1] != observed_store.move_generations[0]
    generation_after_moves = await _current_generation(redis_cache, project_external_id)
    assert generation_after_moves != observed_store.move_generations[1]

    await maintenance.run_delete_batches(
        deleted_paths=("notes/deleted-1.md", "notes/deleted-2.md"),
        batch_size=1,
    )
    assert observed_store.delete_generations[0] == generation_after_moves
    assert observed_store.delete_generations[1] != observed_store.delete_generations[0]
    generation_after_deletes = await _current_generation(redis_cache, project_external_id)
    assert generation_after_deletes != observed_store.delete_generations[1]

    batch_enqueuer = LocalProjectIndexBatchEnqueuer(
        checker=cast(IndexFileBatchChecker, object()),
        reader=cast(IndexFileBatchReader[IndexInputFile], object()),
        indexer=cast(IndexFileBatchIndexer[IndexInputFile], object()),
        content_classifier=cast(IndexFileBatchContentClassifier, object()),
        read_cache=redis_cache.cache,
    )
    project = ProjectRuntimeReference.from_project(test_project)
    await batch_enqueuer.enqueue_index_file_batch(
        RuntimeIndexFileBatchJobRequest(
            project=project,
            batch_index=0,
            batch_count=2,
        )
    )
    generation_after_first_file_batch = await _current_generation(
        redis_cache,
        project_external_id,
    )
    assert generation_after_first_file_batch != generation_after_deletes

    await batch_enqueuer.enqueue_index_file_batch(
        RuntimeIndexFileBatchJobRequest(
            project=project,
            batch_index=1,
            batch_count=2,
        )
    )
    generation_after_second_file_batch = await _current_generation(
        redis_cache,
        project_external_id,
    )
    assert generation_after_second_file_batch != generation_after_first_file_batch


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


@pytest.mark.asyncio
async def test_cancelled_committed_directory_delete_finishes_real_redis_invalidation(
    monkeypatch: pytest.MonkeyPatch,
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """Cancellation during transaction exit cannot skip delete invalidation."""
    _, session_maker = engine_factory
    project_external_id = str(test_project.external_id)
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="cancelled-committed-directory-delete",
    )
    entity_repository = EntityRepository(project_id=test_project.id)
    title = "Cancelled Committed Directory Delete"
    async with db.scoped_session(session_maker) as session:
        await entity_repository.add(
            session,
            Entity(
                title=title,
                note_type="note",
                content_type="text/markdown",
                file_path="cancelled-delete/note.md",
                checksum="cancelled-directory-delete-checksum",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
        )

    blocking_cache = BlockingInvalidationRedisReadCache(
        client=redis_cache.client,
        namespace=redis_cache.namespace,
        prefix=redis_cache.prefix,
    )
    transaction_committed = asyncio.Event()
    hold_after_commit = asyncio.Event()
    original_scoped_session = directory_deletes.db.scoped_session

    @asynccontextmanager
    async def pause_after_commit(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[AsyncSession]:
        async with original_scoped_session(scoped_session_maker) as session:
            yield session
        transaction_committed.set()
        await hold_after_commit.wait()

    monkeypatch.setattr(directory_deletes.db, "scoped_session", pause_after_commit)
    service = DirectoryDeleteService(
        session_maker=session_maker,
        runtime=DirectoryDeleteRuntime(
            store=RepositoryDirectoryDeleteAcceptanceStore(),
            file_delete_enqueuer=GenerationObservingDirectoryDeleteEnqueuer(
                redis_cache,
                project_external_id,
            ),
        ),
    )
    delete_task = asyncio.create_task(
        service.delete_directory(
            project_external_id=project_external_id,
            directory="cancelled-delete",
            read_cache=blocking_cache,
        )
    )

    async with asyncio.timeout(5):
        await transaction_committed.wait()
    delete_task.cancel()
    async with asyncio.timeout(5):
        await blocking_cache.invalidation_started.wait()

    # A second cancellation must not abandon the already-running generation bump.
    delete_task.cancel()
    await asyncio.sleep(0)
    assert not delete_task.done()

    blocking_cache.release_invalidation.set()
    with pytest.raises(asyncio.CancelledError):
        await delete_task

    generation_after = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="cancelled-committed-directory-delete",
    )
    assert generation_after != generation_before

    async with original_scoped_session(session_maker) as session:
        deleted_entities = await entity_repository.get_by_title(session, title)
    assert deleted_entities == []


@pytest.mark.asyncio
async def test_cancelled_committed_directory_move_finishes_real_redis_invalidation(
    monkeypatch: pytest.MonkeyPatch,
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    search_service: SearchService,
    app_config: BasicMemoryConfig,
    project_config: ProjectConfig,
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """Cancellation during a file-move commit cannot skip invalidation."""
    _, session_maker = engine_factory
    entity_repository = EntityRepository(project_id=test_project.id)
    entity_parser = EntityParser(project_config.home)
    file_service = FileService(project_config.home, MarkdownProcessor(entity_parser))
    entity_service = EntityService(
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        observation_repository=ObservationRepository(project_id=test_project.id),
        relation_repository=RelationRepository(project_id=test_project.id),
        file_service=file_service,
        link_resolver=LinkResolver(
            entity_repository,
            search_service,
            session_maker=session_maker,
            app_config=app_config,
        ),
        session_maker=session_maker,
        search_service=search_service,
        app_config=app_config,
    )
    project_external_id = str(test_project.external_id)
    generation_before = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="cancelled-committed-directory-move",
    )
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Cancelled Committed Directory Move",
            directory="cancelled-move-source",
            note_type="note",
            content="Move must remain visible after cancellation.",
        )
    )
    destination_path = "cancelled-move-destination/Cancelled Committed Directory Move.md"
    blocking_cache = BlockingInvalidationRedisReadCache(
        client=redis_cache.client,
        namespace=redis_cache.namespace,
        prefix=redis_cache.prefix,
    )
    transaction_committed = asyncio.Event()
    hold_after_commit = asyncio.Event()
    original_scoped_session = entity_service_module.db.scoped_session

    @asynccontextmanager
    async def pause_after_move_commit(
        scoped_session_maker: async_sessionmaker[AsyncSession],
        existing_session: AsyncSession | None = None,
    ) -> AsyncIterator[AsyncSession]:
        async with original_scoped_session(scoped_session_maker, existing_session) as session:
            yield session
        if transaction_committed.is_set():
            return

        async with original_scoped_session(scoped_session_maker) as session:
            moved_entity = await entity_repository.get_by_id(
                session,
                entity.id,
                load_relations=False,
            )
        if moved_entity is not None and moved_entity.file_path == destination_path:
            transaction_committed.set()
            await hold_after_commit.wait()

    monkeypatch.setattr(entity_service_module.db, "scoped_session", pause_after_move_commit)
    move_task = asyncio.create_task(
        entity_service.move_directory(
            source_directory="cancelled-move-source",
            destination_directory="cancelled-move-destination",
            project_config=project_config,
            app_config=app_config.model_copy(update={"update_permalinks_on_move": False}),
            project_external_id=project_external_id,
            read_cache=blocking_cache,
        )
    )

    async with asyncio.timeout(5):
        await transaction_committed.wait()
    move_task.cancel()
    async with asyncio.timeout(5):
        await blocking_cache.invalidation_started.wait()

    # A second cancellation must not abandon the already-running generation bump.
    move_task.cancel()
    await asyncio.sleep(0)
    assert not move_task.done()

    blocking_cache.release_invalidation.set()
    with pytest.raises(asyncio.CancelledError):
        await move_task

    generation_after = await _initialized_generation(
        redis_cache,
        project_external_id,
        request="cancelled-committed-directory-move",
    )
    assert generation_after != generation_before

    async with original_scoped_session(entity_service.session_maker) as session:
        moved_entity = await entity_repository.get_by_id(
            session,
            entity.id,
            load_relations=False,
        )
    assert moved_entity is not None
    assert moved_entity.file_path == destination_path
