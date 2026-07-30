"""Real Redis coverage for accepted-note materialization invalidation phases."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Protocol

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.index.note_content_materialization import (
    LocalNoteContentMaterializationProvider,
)
from basic_memory.indexing.models import FileIndexOperation, FileIndexResult
from basic_memory.models import Entity, Project
from basic_memory.read_cache import (
    ReadCacheKey,
    ReadCacheOperation,
    read_cache_request_digest,
)
from basic_memory.read_cache.keys import redis_read_cache_generation_key
from basic_memory.read_cache.redis import RedisReadCache
from basic_memory.repository import EntityRepository, NoteContentRepository
from basic_memory.repository.note_content_repository import AcceptedNoteContentWrite
from basic_memory.runtime.note_content import (
    RuntimeAcceptedNoteChange,
    RuntimeAcceptedNoteResponse,
    RuntimePendingNoteMaterialization,
    plan_accepted_note_response,
)
from basic_memory.services.file_service import FileService

pytestmark = pytest.mark.redis


class RedisCacheHarness(Protocol):
    cache: RedisReadCache
    client: Redis
    namespace: str
    prefix: str


class BlockingIndexFileExecutor:
    """Hold indexing after materialization has published its terminal state."""

    def __init__(self, entity: Entity) -> None:
        self.entity = entity
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def index_file(self, file_path: str, *, source: str) -> FileIndexResult:
        del source
        self.started.set()
        await self.release.wait()
        checksum = self.entity.checksum
        if checksum is None:
            raise AssertionError("seeded materialization entity must carry a checksum")
        return FileIndexResult(
            file_path=file_path,
            entity_id=self.entity.id,
            external_id=str(self.entity.external_id),
            title=self.entity.title,
            permalink=self.entity.permalink,
            checksum=checksum,
            operation=FileIndexOperation.updated,
        )


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


async def _initialized_generation(
    redis_cache: RedisCacheHarness,
    project_external_id: str,
) -> bytes | str:
    await redis_cache.cache.lookup(
        ReadCacheKey(
            project_id=project_external_id,
            operation=ReadCacheOperation.entity,
            request_digest=read_cache_request_digest("materialization-publication"),
        )
    )
    return await _current_generation(redis_cache, project_external_id)


async def _accepted_note(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
) -> tuple[
    Entity,
    RuntimeAcceptedNoteChange[RuntimeAcceptedNoteResponse],
]:
    markdown_content = "# Terminal materialization\n"
    checksum = sha256(markdown_content.encode()).hexdigest()
    now = datetime.now(UTC)
    entity_repository = EntityRepository(project_id=project.id)
    content_repository = NoteContentRepository(project_id=project.id)

    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.add(
            session,
            Entity(
                title="Terminal Materialization",
                note_type="note",
                content_type="text/markdown",
                file_path="notes/terminal-materialization.md",
                checksum=checksum,
                created_at=now,
                updated_at=now,
            ),
        )
        await content_repository.accept_write(
            session,
            AcceptedNoteContentWrite(
                entity_id=entity.id,
                markdown_content=markdown_content,
                db_version=1,
                db_checksum=checksum,
                last_source="api",
                updated_at=now,
            ),
        )
        note_content = await content_repository.get_by_entity_id(session, entity.id)
        assert note_content is not None
        payload = plan_accepted_note_response(
            entity=entity,
            note_content=note_content,
            fallback_source="api",
        )

    return entity, RuntimeAcceptedNoteChange(
        status_code=202,
        payload=payload,
        materialization=RuntimePendingNoteMaterialization(
            project_id=project.id,
            entity_id=entity.id,
            db_version=1,
            db_checksum=checksum,
            source="api",
        ),
    )


@pytest.mark.asyncio
async def test_terminal_materialization_invalidates_before_index_completes(
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    test_project: Project,
    redis_cache: RedisCacheHarness,
) -> None:
    """A synced publication advances Redis while the following index is still blocked."""
    _, session_maker = engine_factory
    project_external_id = str(test_project.external_id)
    generation_before = await _initialized_generation(redis_cache, project_external_id)
    entity, accepted = await _accepted_note(session_maker, test_project)
    indexer = BlockingIndexFileExecutor(entity)
    provider = LocalNoteContentMaterializationProvider(
        session_maker=session_maker,
        file_service=FileService(Path(test_project.path)),
        project_external_id=project_external_id,
        read_cache=redis_cache.cache,
        file_indexer=indexer,
        test_mode=True,
    )

    materialization = asyncio.create_task(provider.materialize_write_change(accepted))
    async with asyncio.timeout(5):
        await indexer.started.wait()

    try:
        content_repository = NoteContentRepository(project_id=test_project.id)
        async with db.scoped_session(session_maker) as session:
            note_content = await content_repository.get_by_entity_id(session, entity.id)
        generation_after_publication = await _current_generation(
            redis_cache,
            project_external_id,
        )
    finally:
        indexer.release.set()
        await materialization

    assert note_content is not None
    assert note_content.file_write_status == "synced"
    assert generation_after_publication != generation_before
    generation_after_index = await _current_generation(redis_cache, project_external_id)
    assert generation_after_index != generation_after_publication
