"""SQLite sqlite-vec search repository tests."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode


class StubEmbeddingProvider:
    """Deterministic embedding provider for fast repository tests."""

    model_name = "stub"
    dimensions = 4

    async def embed_query(self, text: str) -> list[float]:
        return self._vectorize(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vectorize(text) for text in texts]

    @staticmethod
    def _vectorize(text: str) -> list[float]:
        normalized = text.lower()
        if any(token in normalized for token in ["auth", "token", "session", "login"]):
            return [1.0, 0.0, 0.0, 0.0]
        if any(token in normalized for token in ["schema", "migration", "database", "sql"]):
            return [0.0, 1.0, 0.0, 0.0]
        if any(token in normalized for token in ["queue", "worker", "async", "task"]):
            return [0.0, 0.0, 1.0, 0.0]
        return [0.0, 0.0, 0.0, 1.0]


def _entity_row(
    *,
    project_id: int,
    row_id: int,
    entity_id: int,
    title: str,
    permalink: str,
    content_stems: str,
) -> SearchIndexRow:
    now = datetime.now(timezone.utc)
    return SearchIndexRow(
        project_id=project_id,
        id=row_id,
        type=SearchItemType.ENTITY.value,
        title=title,
        permalink=permalink,
        file_path=f"{permalink}.md",
        metadata={"note_type": "spec"},
        entity_id=entity_id,
        content_stems=content_stems,
        content_snippet=content_stems,
        created_at=now,
        updated_at=now,
    )


def _enable_semantic(search_repository: SQLiteSearchRepository) -> None:
    try:
        import sqlite_vec  # noqa: F401
    except ImportError:
        pytest.skip("sqlite-vec dependency is required for sqlite vector repository tests.")

    search_repository._semantic_enabled = True
    search_repository._embedding_provider = StubEmbeddingProvider()
    search_repository._vector_dimensions = search_repository._embedding_provider.dimensions
    search_repository._vector_tables_initialized = False


@pytest.mark.asyncio
async def test_sqlite_vec_tables_are_created_and_rebuilt(search_repository):
    """Repository rebuilds vector schema deterministically on mismatch."""
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("sqlite-vec repository behavior is local SQLite-only.")

    _enable_semantic(search_repository)
    await search_repository.init_search_index()

    async with db.scoped_session(search_repository.session_maker) as session:
        await session.execute(text("DROP TABLE IF EXISTS search_vector_embeddings"))
        await session.execute(text("DROP TABLE IF EXISTS search_vector_chunks"))
        await session.execute(text("CREATE TABLE search_vector_chunks (id INTEGER PRIMARY KEY)"))
        await session.commit()

    search_repository._vector_tables_initialized = False
    await search_repository.sync_entity_vectors(99999)

    async with db.scoped_session(search_repository.session_maker) as session:
        columns_result = await session.execute(text("PRAGMA table_info(search_vector_chunks)"))
        columns = {row[1] for row in columns_result.fetchall()}
        assert columns == {
            "id",
            "entity_id",
            "project_id",
            "chunk_key",
            "chunk_text",
            "source_hash",
            "updated_at",
        }

        table_result = await session.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'search_vector_embeddings'"
            )
        )
        assert table_result.scalar_one() == "search_vector_embeddings"


@pytest.mark.asyncio
async def test_sqlite_chunk_upsert_and_delete_lifecycle(search_repository):
    """sync_entity_vectors updates changed chunks and clears vectors when source rows disappear."""
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("sqlite-vec repository behavior is local SQLite-only.")

    _enable_semantic(search_repository)
    await search_repository.init_search_index()

    await search_repository.index_item(
        _entity_row(
            project_id=search_repository.project_id,
            row_id=101,
            entity_id=101,
            title="Auth Design",
            permalink="specs/auth-design",
            content_stems="auth token session login flow",
        )
    )
    await search_repository.sync_entity_vectors(101)

    async with db.scoped_session(search_repository.session_maker) as session:
        initial = await session.execute(
            text(
                "SELECT COUNT(*) FROM search_vector_chunks "
                "WHERE project_id = :project_id AND entity_id = :entity_id"
            ),
            {"project_id": search_repository.project_id, "entity_id": 101},
        )
        assert int(initial.scalar_one()) >= 1

    await search_repository.index_item(
        _entity_row(
            project_id=search_repository.project_id,
            row_id=101,
            entity_id=101,
            title="Auth Design",
            permalink="specs/auth-design",
            content_stems="auth token rotation and session revocation model",
        )
    )
    await search_repository.sync_entity_vectors(101)
    await search_repository.delete_by_entity_id(101)
    await search_repository.sync_entity_vectors(101)

    async with db.scoped_session(search_repository.session_maker) as session:
        chunk_count = await session.execute(
            text(
                "SELECT COUNT(*) FROM search_vector_chunks "
                "WHERE project_id = :project_id AND entity_id = :entity_id"
            ),
            {"project_id": search_repository.project_id, "entity_id": 101},
        )
        assert int(chunk_count.scalar_one()) == 0

        embedding_count = await session.execute(
            text(
                "SELECT COUNT(*) FROM search_vector_embeddings "
                "WHERE rowid IN ("
                "SELECT id FROM search_vector_chunks "
                "WHERE project_id = :project_id AND entity_id = :entity_id"
                ")"
            ),
            {"project_id": search_repository.project_id, "entity_id": 101},
        )
        assert int(embedding_count.scalar_one()) == 0


@pytest.mark.asyncio
async def test_sqlite_vector_search_returns_ranked_entities(search_repository):
    """Vector mode ranks entities using sqlite-vec nearest-neighbor search."""
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("sqlite-vec repository behavior is local SQLite-only.")

    _enable_semantic(search_repository)
    await search_repository.init_search_index()
    await search_repository.bulk_index_items(
        [
            _entity_row(
                project_id=search_repository.project_id,
                row_id=201,
                entity_id=201,
                title="Authentication Decisions",
                permalink="specs/authentication",
                content_stems="login session token refresh auth design",
            ),
            _entity_row(
                project_id=search_repository.project_id,
                row_id=202,
                entity_id=202,
                title="Database Migrations",
                permalink="specs/migrations",
                content_stems="alembic sqlite postgres schema migration ddl",
            ),
        ]
    )
    await search_repository.sync_entity_vectors(201)
    await search_repository.sync_entity_vectors(202)

    results = await search_repository.search(
        search_text="session token auth",
        retrieval_mode=SearchRetrievalMode.VECTOR,
        limit=5,
        offset=0,
    )

    assert results
    assert results[0].permalink == "specs/authentication"
    assert all(result.type == SearchItemType.ENTITY.value for result in results)


@pytest.mark.asyncio
async def test_sqlite_hybrid_search_combines_fts_and_vector(search_repository):
    """Hybrid mode fuses FTS and vector results with RRF."""
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("sqlite-vec repository behavior is local SQLite-only.")

    _enable_semantic(search_repository)
    await search_repository.init_search_index()
    await search_repository.bulk_index_items(
        [
            _entity_row(
                project_id=search_repository.project_id,
                row_id=301,
                entity_id=301,
                title="Task Queue Worker",
                permalink="specs/task-queue-worker",
                content_stems="queue worker retries async processing",
            ),
            _entity_row(
                project_id=search_repository.project_id,
                row_id=302,
                entity_id=302,
                title="Search Index Notes",
                permalink="specs/search-index",
                content_stems="fts bm25 ranking vector search hybrid rrf",
            ),
        ]
    )
    await search_repository.sync_entity_vectors(301)
    await search_repository.sync_entity_vectors(302)

    results = await search_repository.search(
        search_text="hybrid vector search",
        retrieval_mode=SearchRetrievalMode.HYBRID,
        limit=5,
        offset=0,
    )

    assert results
    assert any(result.permalink == "specs/search-index" for result in results)
