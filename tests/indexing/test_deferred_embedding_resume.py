"""Deferred oversized-entity embeddings resume on later index passes (#1605).

An entity with more pending chunks than one shard is embedded a shard at a
time, and `entity.vector_sync_deferred_at` records that work is still owed.
These tests run the real repository sharding against a real database to prove
that the next project-index embedding pass picks that entity up again even when
no file changed, and that the marker clears once the entity is complete.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.index.local_project import LocalDeferredEmbeddingTargetSource
from basic_memory.indexing.embedding_index_planning import (
    EmbeddingIndexTarget,
    RepositoryVectorSyncEntitySource,
)
from basic_memory.indexing.project_index_coordinator import sync_project_index_vector_targets
from basic_memory.models import Entity, Project
from basic_memory.repository import search_repository_base as search_repository_base_module
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_repository_base import SearchRepositoryBase
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.runtime.jobs import RuntimeProjectIndexJobRequest
from basic_memory.runtime.projects import ProjectRuntimeReference
from basic_memory.schemas.search import SearchItemType
from basic_memory.services import FileService
from basic_memory.services.search_service import SearchService

# Five sized sections plus the title chunk: six chunks, three shards of two.
OVERSIZED_SECTION_COUNT = 5
OVERSIZED_CHUNK_COUNT = OVERSIZED_SECTION_COUNT + 1
TEST_SHARD_SIZE = 2


class StubEmbeddingProvider:
    """Deterministic four-dimension provider so no model is loaded."""

    model_name = "stub"
    dimensions = 4

    async def embed_query(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0, 1.0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 0.0, 0.0, 1.0] for _ in texts]

    def runtime_log_attrs(self) -> dict[str, object]:
        return {}


def _oversized_content() -> str:
    """Content whose heading sections each become one vector chunk."""
    filler = "x" * 860
    return "\n\n".join(
        f"## Part {index}\n{filler}" for index in range(1, OVERSIZED_SECTION_COUNT + 1)
    )


async def _semantic_repository(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
    app_config: BasicMemoryConfig,
) -> SearchRepositoryBase:
    """Build the backend's search repository with semantic search on a stub provider."""
    app_config.semantic_search_enabled = True
    if app_config.database_backend == DatabaseBackend.POSTGRES:
        async with db.scoped_session(session_maker) as session:
            try:
                await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await session.commit()
            except DBAPIError:
                pytest.skip("pgvector extension is unavailable in this Postgres environment.")
        repository: SearchRepositoryBase = PostgresSearchRepository(
            session_maker,
            project_id=project.id,
            app_config=app_config,
            embedding_provider=StubEmbeddingProvider(),
        )
    else:
        pytest.importorskip("sqlite_vec")
        repository = SQLiteSearchRepository(
            session_maker,
            project_id=project.id,
            app_config=app_config,
            embedding_provider=StubEmbeddingProvider(),
        )
    await repository.init_search_index()
    # Some Python builds import sqlite_vec but cannot load SQLite extensions;
    # init then falls back to keyword-only search instead of raising (#711).
    if not repository._semantic_enabled:
        pytest.skip("SQLite extension loading is unavailable; semantic search fell back.")
    return repository


async def _oversized_entity(
    session_maker: async_sessionmaker[AsyncSession],
    entity_repository: EntityRepository,
    repository: SearchRepositoryBase,
) -> Entity:
    """Create an indexed entity that produces more chunks than one shard."""
    now = datetime.now(timezone.utc)
    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.create(
            session,
            {
                "project_id": entity_repository.project_id,
                "title": "Oversized Note",
                "note_type": "note",
                "permalink": "notes/oversized-note",
                "file_path": "notes/oversized-note.md",
                "content_type": "text/markdown",
                "checksum": "oversized-checksum",
                "created_at": now,
                "updated_at": now,
            },
        )
    content = _oversized_content()
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


async def _deferred_at(
    session_maker: async_sessionmaker[AsyncSession], entity_id: int
) -> datetime | None:
    async with db.scoped_session(session_maker) as session:
        result = await session.execute(
            text("SELECT vector_sync_deferred_at FROM entity WHERE id = :id"),
            {"id": entity_id},
        )
        return result.scalar_one()


async def _chunk_count(session_maker: async_sessionmaker[AsyncSession], entity_id: int) -> int:
    async with db.scoped_session(session_maker) as session:
        result = await session.execute(
            text("SELECT COUNT(*) FROM search_vector_chunks WHERE entity_id = :id"),
            {"id": entity_id},
        )
        return int(result.scalar_one())


def _index_request(project: Project) -> RuntimeProjectIndexJobRequest:
    return RuntimeProjectIndexJobRequest(
        project=ProjectRuntimeReference(
            project_id=project.id,
            project_external_id=str(project.external_id),
            project_path=project.path,
            project_name=project.name,
            project_permalink=project.permalink,
        ),
    )


@pytest.mark.asyncio
async def test_unchanged_oversized_entity_finishes_across_index_passes(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    app_config: BasicMemoryConfig,
    entity_repository: EntityRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A note over the shard limit completes without an edit or a manual reindex."""
    monkeypatch.setattr(
        search_repository_base_module, "OVERSIZED_ENTITY_VECTOR_SHARD_SIZE", TEST_SHARD_SIZE
    )
    repository = await _semantic_repository(session_maker, test_project, app_config)
    entity = await _oversized_entity(session_maker, entity_repository, repository)
    deferred_source = RepositoryVectorSyncEntitySource(
        session_maker=session_maker, project_id=test_project.id
    )

    # The write that created the note embeds its first shard and defers the rest.
    first = await repository.sync_entity_vectors_batch([entity.id])
    assert first.entities_deferred == 1
    assert await _deferred_at(session_maker, entity.id) is not None
    assert await deferred_source.list_deferred_embedding_targets() == (
        EmbeddingIndexTarget(entity_id=entity.id, entity_checksum="oversized-checksum"),
    )

    # Later index passes find no changed files, so they carry no batch targets.
    # Each one advances the deferred entity by one shard.
    await sync_project_index_vector_targets(
        request=_index_request(test_project),
        batch_results=(),
        embedding_vector_sync=repository,
        deferred_embedding_targets=deferred_source,
    )
    assert await _deferred_at(session_maker, entity.id) is not None
    assert await _chunk_count(session_maker, entity.id) == 2 * TEST_SHARD_SIZE

    await sync_project_index_vector_targets(
        request=_index_request(test_project),
        batch_results=(),
        embedding_vector_sync=repository,
        deferred_embedding_targets=deferred_source,
    )

    assert await _chunk_count(session_maker, entity.id) == OVERSIZED_CHUNK_COUNT
    assert await _deferred_at(session_maker, entity.id) is None
    assert await deferred_source.list_deferred_embedding_targets() == ()


@pytest.mark.asyncio
async def test_index_pass_without_deferred_source_leaves_deferral_alone(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    app_config: BasicMemoryConfig,
    entity_repository: EntityRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtimes that do not wire the source (cloud fan-out) keep their old behavior."""
    monkeypatch.setattr(
        search_repository_base_module, "OVERSIZED_ENTITY_VECTOR_SHARD_SIZE", TEST_SHARD_SIZE
    )
    repository = await _semantic_repository(session_maker, test_project, app_config)
    entity = await _oversized_entity(session_maker, entity_repository, repository)
    await repository.sync_entity_vectors_batch([entity.id])

    await sync_project_index_vector_targets(
        request=_index_request(test_project),
        batch_results=(),
        embedding_vector_sync=repository,
    )

    assert await _chunk_count(session_maker, entity.id) == TEST_SHARD_SIZE
    assert await _deferred_at(session_maker, entity.id) is not None


@pytest.mark.asyncio
async def test_keyword_only_fallback_does_not_resume_deferred_entities(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    app_config: BasicMemoryConfig,
    entity_repository: EntityRepository,
    file_service: FileService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A runtime that fell back to keyword-only search leaves old markers alone."""
    monkeypatch.setattr(
        search_repository_base_module, "OVERSIZED_ENTITY_VECTOR_SHARD_SIZE", TEST_SHARD_SIZE
    )
    repository = await _semantic_repository(session_maker, test_project, app_config)
    entity = await _oversized_entity(session_maker, entity_repository, repository)
    await repository.sync_entity_vectors_batch([entity.id])
    local_source = LocalDeferredEmbeddingTargetSource(
        targets=RepositoryVectorSyncEntitySource(
            session_maker=session_maker, project_id=test_project.id
        ),
        # The local runtime wires the search service, which asks its repository.
        semantic_runtime=SearchService(repository, entity_repository, file_service, session_maker),
    )
    assert await local_source.list_deferred_embedding_targets() == (
        EmbeddingIndexTarget(entity_id=entity.id, entity_checksum="oversized-checksum"),
    )

    # What init_search_index() records when the vector runtime cannot load (#711).
    repository._semantic_enabled = False

    assert await local_source.list_deferred_embedding_targets() == ()
    # The pass has nothing to embed, so it does not reach the disabled vector sync.
    await sync_project_index_vector_targets(
        request=_index_request(test_project),
        batch_results=(),
        embedding_vector_sync=repository,
        deferred_embedding_targets=local_source,
    )
    assert await _deferred_at(session_maker, entity.id) is not None
