"""An explicit vector reindex drains deferred shards before reporting (#1726).

The repository embeds at most one shard of an oversized entity per pass and
defers the rest. `reindex_vectors` is the explicit rebuild, so it must keep
going until those entities finish, and report anything still owed instead of
counting it as done. These tests run the real repository sharding against a
real database with a deterministic stub embedding provider.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.models import Entity, Project
from basic_memory.repository import search_repository_base as search_repository_base_module
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_repository_base import SearchRepositoryBase
from basic_memory.runtime.vector_sync import VectorSyncBatchResult
from basic_memory.schemas.search import SearchItemType
from basic_memory.services import FileService
from basic_memory.services.search_service import SearchService
from tests.indexing.test_deferred_embedding_resume import (
    _chunk_count,
    _deferred_at,
    _semantic_repository,
)


async def _indexed_entity(
    session_maker: async_sessionmaker[AsyncSession],
    entity_repository: EntityRepository,
    repository: SearchRepositoryBase,
    *,
    name: str,
    sections: int,
) -> Entity:
    """Create an indexed entity whose heading sections each embed as one chunk."""
    now = datetime.now(timezone.utc)
    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.create(
            session,
            {
                "project_id": entity_repository.project_id,
                "title": name,
                "note_type": "note",
                "permalink": f"notes/{name}",
                "file_path": f"notes/{name}.md",
                "content_type": "text/markdown",
                "checksum": f"{name}-checksum",
                "created_at": now,
                "updated_at": now,
            },
        )
    filler = "x" * 860
    content = "\n\n".join(f"## Part {index}\n{filler}" for index in range(1, sections + 1))
    await repository.index_item(
        SearchIndexRow(
            project_id=repository.project_id,
            id=entity.id,
            type=SearchItemType.ENTITY.value,
            title=entity.title,
            permalink=entity.permalink,
            file_path=entity.file_path,
            metadata={"note_type": "note"},
            entity_id=entity.id,
            content_stems=content,
            content_snippet=content,
            created_at=now,
            updated_at=now,
        )
    )
    return entity


async def _assert_nothing_pending(repository: SearchRepositoryBase, entity_id: int) -> None:
    """A follow-up sync finds every chunk embedded and current."""
    follow_up = await repository.sync_entity_vectors_batch([entity_id])
    assert follow_up.embedding_jobs_total == 0
    assert follow_up.entities_deferred == 0
    assert follow_up.entities_skipped == 1


async def _search_service(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    app_config: BasicMemoryConfig,
    entity_repository: EntityRepository,
    file_service: FileService,
) -> tuple[SearchService, SearchRepositoryBase]:
    repository = await _semantic_repository(session_maker, test_project, app_config)
    service = SearchService(repository, entity_repository, file_service, session_maker)
    return service, repository


# Each case keeps five chunks (one per section) and moves the shard boundary
# around them: exactly one shard, one chunk over, and three shards. The issue's
# 256 / 257 / 513 boundaries, scaled down.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shard_size", "force_full"),
    [
        (5, False),
        (5, True),
        (4, False),
        (4, True),
        (2, False),
        (2, True),
    ],
)
async def test_reindex_drains_every_shard_of_an_oversized_entity(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    app_config: BasicMemoryConfig,
    entity_repository: EntityRepository,
    file_service: FileService,
    monkeypatch: pytest.MonkeyPatch,
    shard_size: int,
    force_full: bool,
) -> None:
    """One reindex embeds every chunk, whatever the shard boundary or mode."""
    monkeypatch.setattr(
        search_repository_base_module, "OVERSIZED_ENTITY_VECTOR_SHARD_SIZE", shard_size
    )
    service, repository = await _search_service(
        session_maker, test_project, app_config, entity_repository, file_service
    )
    entity = await _indexed_entity(
        session_maker, entity_repository, repository, name="oversized", sections=5
    )

    stats = await service.reindex_vectors(force_full=force_full)

    assert await _chunk_count(session_maker, entity.id) == 5
    assert await _deferred_at(session_maker, entity.id) is None
    await _assert_nothing_pending(repository, entity.id)
    assert stats["embedded"] == 1
    assert stats["deferred"] == 0
    assert stats["errors"] == 0


@pytest.mark.asyncio
async def test_repeated_full_reindex_completes_each_time(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    app_config: BasicMemoryConfig,
    entity_repository: EntityRepository,
    file_service: FileService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full rebuild clears once, then drains; repeating it is not stuck on shard one."""
    monkeypatch.setattr(search_repository_base_module, "OVERSIZED_ENTITY_VECTOR_SHARD_SIZE", 2)
    service, repository = await _search_service(
        session_maker, test_project, app_config, entity_repository, file_service
    )
    entity = await _indexed_entity(
        session_maker, entity_repository, repository, name="oversized", sections=5
    )

    for _ in range(2):
        stats = await service.reindex_vectors(force_full=True)
        assert await _chunk_count(session_maker, entity.id) == 5
        assert stats["embedded"] == 1
        assert stats["deferred"] == 0

    # An incremental pass afterwards finds the entity unchanged and skips it.
    stats = await service.reindex_vectors()
    assert stats["skipped"] == 1
    assert stats["deferred"] == 0
    await _assert_nothing_pending(repository, entity.id)


@pytest.mark.asyncio
async def test_reindex_drains_several_entities_and_counts_each_once(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    app_config: BasicMemoryConfig,
    entity_repository: EntityRepository,
    file_service: FileService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entities that finish on different passes are each counted as embedded once."""
    monkeypatch.setattr(search_repository_base_module, "OVERSIZED_ENTITY_VECTOR_SHARD_SIZE", 2)
    service, repository = await _search_service(
        session_maker, test_project, app_config, entity_repository, file_service
    )
    small = await _indexed_entity(
        session_maker, entity_repository, repository, name="small", sections=1
    )
    medium = await _indexed_entity(
        session_maker, entity_repository, repository, name="medium", sections=3
    )
    large = await _indexed_entity(
        session_maker, entity_repository, repository, name="large", sections=7
    )
    progress: list[tuple[int, int, int]] = []

    stats = await service.reindex_vectors(
        progress_callback=lambda entity_id, completed, total: progress.append(
            (entity_id, completed, total)
        )
    )

    for entity in (small, medium, large):
        assert await _deferred_at(session_maker, entity.id) is None
        await _assert_nothing_pending(repository, entity.id)
    assert await _chunk_count(session_maker, large.id) == 7
    assert stats == {
        **stats,
        "total_entities": 3,
        "embedded": 3,
        "skipped": 0,
        "errors": 0,
        "deferred": 0,
    }
    # Progress describes the first pass over every entity; continuations do not
    # re-report entities the caller already counted.
    assert [completed for _, completed, _ in progress] == [1, 2, 3]
    assert {total for _, _, total in progress} == {3}


@pytest.mark.asyncio
async def test_reindex_stops_a_stalled_drain_and_reports_deferred(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    app_config: BasicMemoryConfig,
    entity_repository: EntityRepository,
    file_service: FileService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A continuation that stops shrinking the pending work ends the drain.

    The stuck entity is reported as deferred, not embedded, and the loop does not
    spin forever on work it cannot advance.
    """
    service, repository = await _search_service(
        session_maker, test_project, app_config, entity_repository, file_service
    )
    entity = await _indexed_entity(
        session_maker, entity_repository, repository, name="stuck", sections=1
    )
    calls: list[list[int]] = []

    async def stalled_sync(entity_ids: list[int], progress_callback=None) -> VectorSyncBatchResult:
        calls.append(list(entity_ids))
        # Every pass sees the same four pending chunks and defers the entity again.
        return VectorSyncBatchResult(
            entities_total=len(entity_ids),
            entities_synced=0,
            entities_failed=0,
            entities_deferred=1,
            deferred_entity_ids=(entity.id,),
            chunks_total=10,
            chunks_skipped=6,
            embedding_jobs_total=2,
        )

    monkeypatch.setattr(repository, "sync_entity_vectors_batch", stalled_sync)

    stats = await service.reindex_vectors()

    # The first pass, one continuation that sets the baseline, and one that shows
    # no progress.
    assert calls == [[entity.id], [entity.id], [entity.id]]
    assert stats["embedded"] == 0
    assert stats["errors"] == 0
    assert stats["deferred"] == 1


@pytest.mark.asyncio
async def test_reindex_counts_an_entity_that_fails_during_the_drain(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    app_config: BasicMemoryConfig,
    entity_repository: EntityRepository,
    file_service: FileService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deferred entity that fails on a continuation is an error, not deferred work."""
    service, repository = await _search_service(
        session_maker, test_project, app_config, entity_repository, file_service
    )
    entity = await _indexed_entity(
        session_maker, entity_repository, repository, name="flaky", sections=1
    )
    passes = iter(
        [
            VectorSyncBatchResult(
                entities_total=1,
                entities_synced=0,
                entities_failed=0,
                entities_deferred=1,
                deferred_entity_ids=(entity.id,),
                chunks_total=10,
                chunks_skipped=0,
            ),
            VectorSyncBatchResult(
                entities_total=1,
                entities_synced=0,
                entities_failed=1,
                failed_entity_ids=(entity.id,),
                sample_errors=("provider timed out",),
                chunks_total=10,
                chunks_skipped=2,
            ),
        ]
    )

    async def failing_sync(entity_ids: list[int], progress_callback=None) -> VectorSyncBatchResult:
        return next(passes)

    monkeypatch.setattr(repository, "sync_entity_vectors_batch", failing_sync)

    stats = await service.reindex_vectors()

    assert stats["errors"] == 1
    assert stats["deferred"] == 0
    assert stats["sample_errors"] == ("provider timed out",)
