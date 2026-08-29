"""PostgreSQL regressions for semantic vector generation ownership."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.semantic_vector_index import (
    VectorDeletion,
    VectorIndexScope,
    VectorKey,
    VectorMatch,
    VectorRecord,
)
from basic_memory.schemas.search import SearchItemType


pytestmark = pytest.mark.postgres


class CoordinatedEmbeddingProvider:
    """Deterministic provider that can pause one vector sync after prepare."""

    model_name = "generation-ownership-test"
    dimensions = 4

    def __init__(
        self,
        *,
        started: asyncio.Event | None = None,
        resume: asyncio.Event | None = None,
    ) -> None:
        self.started = started
        self.resume = resume

    async def embed_query(self, text: str) -> list[float]:
        return self._vectorize(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.started is not None:
            assert self.resume is not None
            self.started.set()
            await self.resume.wait()
        return [self._vectorize(text) for text in texts]

    def runtime_log_attrs(self) -> dict[str, object]:
        return {}

    @staticmethod
    def _vectorize(text: str) -> list[float]:
        bucket = len(text) % 4
        return [1.0 if index == bucket else 0.0 for index in range(4)]


class InMemoryExternalVectorIndex:
    """Generation-aware external adapter used behind Core's manifest contract."""

    def __init__(self, scope: VectorIndexScope) -> None:
        self._scope = scope
        self.records: dict[VectorKey, VectorRecord] = {}

    @property
    def scope(self) -> VectorIndexScope:
        return self._scope

    async def initialize(self) -> None:
        return None

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        for record in records:
            self.records[record.key] = record

    async def delete(self, records: Sequence[VectorDeletion]) -> None:
        for deletion in records:
            current = self.records.get(deletion.key)
            if current is not None and current.source_hash == deletion.source_hash:
                self.records.pop(deletion.key)

    async def delete_entity(self, entity_id: int) -> None:
        self.records = {
            key: record for key, record in self.records.items() if key.entity_id != entity_id
        }

    async def search(
        self,
        query: Sequence[float],
        *,
        limit: int,
    ) -> list[VectorMatch]:
        return []


@pytest.fixture(autouse=True)
def _require_postgres_backend(db_backend: str) -> None:
    """These concurrency regressions need PostgreSQL transaction semantics."""
    if db_backend != "postgres":
        pytest.skip("Vector generation ownership tests require BASIC_MEMORY_TEST_POSTGRES=1")


async def _skip_if_pgvector_unavailable(session_maker) -> None:
    async with db.scoped_session(session_maker) as session:
        try:
            await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await session.commit()
        except Exception:
            pytest.skip("pgvector extension is unavailable in this PostgreSQL test environment")


def _app_config() -> BasicMemoryConfig:
    return BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        database_backend=DatabaseBackend.POSTGRES,
        semantic_search_enabled=True,
    )


async def _repositories(
    session_maker,
    *,
    project_id: int,
    vector_backend: str,
    blocking_provider: CoordinatedEmbeddingProvider,
) -> tuple[
    PostgresSearchRepository,
    PostgresSearchRepository,
    InMemoryExternalVectorIndex | None,
]:
    vector_index: InMemoryExternalVectorIndex | None = None
    vector_index_name: str | None = None
    if vector_backend == "pgvector":
        await _skip_if_pgvector_unavailable(session_maker)
    else:
        vector_index_name = "test-external"
        vector_index = InMemoryExternalVectorIndex(
            VectorIndexScope(
                namespace="generation-ownership",
                project_id=project_id,
                embedding_identity="test:4",
                dimensions=4,
            )
        )

    app_config = _app_config()
    owner = PostgresSearchRepository(
        session_maker,
        project_id,
        app_config=app_config,
        embedding_provider=blocking_provider,
        vector_index_name=vector_index_name,
        vector_index=vector_index,
    )
    successor = PostgresSearchRepository(
        session_maker,
        project_id,
        app_config=app_config,
        embedding_provider=CoordinatedEmbeddingProvider(),
        vector_index_name=vector_index_name,
        vector_index=vector_index,
    )
    return owner, successor, vector_index


async def _index_entity(
    repository: PostgresSearchRepository,
    *,
    entity_id: int,
    content: str,
) -> None:
    now = datetime.now(timezone.utc)
    await repository.index_item(
        SearchIndexRow(
            project_id=repository.project_id,
            id=entity_id,
            title="Vector Generation Ownership",
            content_stems=content,
            content_snippet=content,
            permalink="specs/vector-generation-ownership",
            file_path="specs/vector-generation-ownership.md",
            type=SearchItemType.ENTITY.value,
            entity_id=entity_id,
            metadata={"note_type": "spec"},
            created_at=now,
            updated_at=now,
        )
    )


def _vector_chunk_section(heading: str, body: str = "vector sync section body content.") -> str:
    """Build one heading section sized to be its own vector chunk.

    Vector chunks split on headings and the size budget, not per bullet, so
    multi-chunk fixtures use sized heading sections: each is long enough that
    adjacent sections never pack into one chunk, yet short enough to avoid the
    long-section windower, so N sections yield N chunks.
    """
    return f"## {heading}\n" + " ".join([body] * 15)


def _generation_content(*headings: str) -> str:
    """Join sized heading sections into one entity body (one chunk per heading)."""
    return "\n\n".join(_vector_chunk_section(heading) for heading in headings)


async def _manifest_rows(session_maker, *, project_id: int, entity_id: int):
    async with db.scoped_session(session_maker) as session:
        result = await session.execute(
            text(
                "SELECT id, chunk_key, source_hash, embedding_status "
                "FROM search_vector_chunks "
                "WHERE project_id = :project_id AND entity_id = :entity_id "
                "ORDER BY chunk_key"
            ),
            {"project_id": project_id, "entity_id": entity_id},
        )
        return result.mappings().all()


@pytest.mark.asyncio
@pytest.mark.parametrize("vector_backend", ["pgvector", "external"])
async def test_superseded_manifest_generation_defers_old_job_without_failure(
    session_maker,
    test_project,
    vector_backend: str,
) -> None:
    """A newer generation owns convergence after deleting job A's prepared rows."""
    embed_started = asyncio.Event()
    resume_embedding = asyncio.Event()
    owner, successor, external_index = await _repositories(
        session_maker,
        project_id=test_project.id,
        vector_backend=vector_backend,
        blocking_provider=CoordinatedEmbeddingProvider(
            started=embed_started,
            resume=resume_embedding,
        ),
    )
    entity_id = 1200
    await _index_entity(
        owner,
        entity_id=entity_id,
        content=_generation_content("Alpha", "Beta", "Gamma", "Delta"),
    )

    owner_task = asyncio.create_task(owner.sync_entity_vectors_batch([entity_id]))
    try:
        await asyncio.wait_for(embed_started.wait(), timeout=10)
        prepared_rows = await _manifest_rows(
            session_maker,
            project_id=test_project.id,
            entity_id=entity_id,
        )
        assert len(prepared_rows) >= 4
        assert {row["embedding_status"] for row in prepared_rows} == {"pending"}

        await _index_entity(
            successor,
            entity_id=entity_id,
            content=_generation_content("Current", "State"),
        )
        successor_result = await successor.sync_entity_vectors_batch([entity_id])
        assert successor_result.entities_synced == 1
        assert successor_result.entities_failed == 0

        resume_embedding.set()
        owner_result = await asyncio.wait_for(owner_task, timeout=10)
    finally:
        resume_embedding.set()
        if not owner_task.done():
            owner_task.cancel()
            await asyncio.gather(owner_task, return_exceptions=True)

    assert owner_result.entities_synced == 0
    assert owner_result.entities_failed == 0
    assert owner_result.entities_deferred == 1

    final_rows = await _manifest_rows(
        session_maker,
        project_id=test_project.id,
        entity_id=entity_id,
    )
    assert final_rows
    assert len(final_rows) < len(prepared_rows)
    assert {row["embedding_status"] for row in final_rows} == {"ready"}

    manifest_generations = {
        VectorKey(entity_id=entity_id, chunk_key=str(row["chunk_key"])): str(row["source_hash"])
        for row in final_rows
    }
    if external_index is not None:
        assert {
            key: record.source_hash for key, record in external_index.records.items()
        } == manifest_generations
    else:
        async with db.scoped_session(session_maker) as session:
            parity = await session.execute(
                text(
                    "SELECT COUNT(*) AS manifest_count, COUNT(e.chunk_id) AS embedding_count, "
                    "COUNT(*) FILTER (WHERE e.source_hash != c.source_hash) AS hash_mismatches "
                    "FROM search_vector_chunks c "
                    "LEFT JOIN search_vector_embeddings e ON e.chunk_id = c.id "
                    "WHERE c.project_id = :project_id AND c.entity_id = :entity_id"
                ),
                {"project_id": test_project.id, "entity_id": entity_id},
            )
            counts = parity.mappings().one()
        assert int(counts["manifest_count"]) == int(counts["embedding_count"])
        assert int(counts["hash_mismatches"]) == 0

    retry_result = await successor.sync_entity_vectors_batch([entity_id])
    assert retry_result.entities_synced == 1
    assert retry_result.entities_failed == 0
    assert retry_result.entities_skipped == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("vector_backend", ["pgvector", "external"])
async def test_missing_current_manifest_generation_remains_a_correctness_error(
    session_maker,
    test_project,
    vector_backend: str,
) -> None:
    """A deleted row is not stale work while its prepared generation is still current."""
    embed_started = asyncio.Event()
    resume_embedding = asyncio.Event()
    owner, _successor, _external_index = await _repositories(
        session_maker,
        project_id=test_project.id,
        vector_backend=vector_backend,
        blocking_provider=CoordinatedEmbeddingProvider(
            started=embed_started,
            resume=resume_embedding,
        ),
    )
    entity_id = 1201
    await _index_entity(
        owner,
        entity_id=entity_id,
        content="# Current Generation\n- this source is still authoritative",
    )

    owner_task = asyncio.create_task(owner.sync_entity_vectors(entity_id))
    try:
        await asyncio.wait_for(embed_started.wait(), timeout=10)
        async with db.scoped_session(session_maker) as session:
            await session.execute(
                text(
                    "DELETE FROM search_vector_chunks "
                    "WHERE project_id = :project_id AND entity_id = :entity_id"
                ),
                {"project_id": test_project.id, "entity_id": entity_id},
            )
            await session.commit()

        resume_embedding.set()
        with pytest.raises(RuntimeError, match="Vector manifest rows disappeared before write"):
            await asyncio.wait_for(owner_task, timeout=10)
    finally:
        resume_embedding.set()
        if not owner_task.done():
            owner_task.cancel()
            await asyncio.gather(owner_task, return_exceptions=True)
