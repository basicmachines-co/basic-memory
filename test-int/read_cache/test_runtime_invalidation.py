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
from basic_memory.index.local_moves import (
    LocalMoveEntityRepository,
    LocalWatchMoveProcessor,
)
from basic_memory.indexing.project_index_maintenance import (
    ProjectIndexDeleteRun,
    ProjectIndexMoveRun,
)
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
from basic_memory.runtime.storage import (
    STORAGE_OBJECT_DELETED_EVENT,
    StorageEventPayload,
    StorageObjectIdentity,
    StorageObjectVersion,
)
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
    entity_repository = EntityRepository(project_id=test_project.id)
    content_repository = NoteContentRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.add(
            session,
            Entity(
                title="Recovered Note",
                note_type="note",
                content_type="text/markdown",
                file_path="notes/recovered.md",
                checksum="entity-checksum-1",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
        )
        await content_repository.accept_write(
            session,
            AcceptedNoteContentWrite(
                entity_id=entity.id,
                markdown_content="# Recovered with cache invalidation\n",
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
