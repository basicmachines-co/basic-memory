"""Integration tests for PostgresSearchRepository.

These tests only run in Postgres mode (testcontainers) and ensure that the
Postgres tsvector-backed search implementation remains well covered.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository.postgres_search_repository import (
    PostgresSearchRepository,
    _strip_nul_from_row,
)
from basic_memory.repository.semantic_errors import SemanticSearchDisabledError
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode


pytestmark = pytest.mark.postgres


class StubEmbeddingProvider:
    """Deterministic embedding provider for Postgres semantic tests."""

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


async def _skip_if_pgvector_unavailable(session_maker) -> None:
    """Skip semantic pgvector tests when extension is not available in test Postgres image."""
    async with db.scoped_session(session_maker) as session:
        try:
            await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await session.commit()
        except Exception:
            pytest.skip("pgvector extension is unavailable in this Postgres test environment.")


@pytest.fixture(autouse=True)
def _require_postgres_backend(db_backend):
    """Ensure these tests never run under SQLite."""
    if db_backend != "postgres":
        pytest.skip("PostgresSearchRepository tests require BASIC_MEMORY_TEST_POSTGRES=1")


@pytest.mark.asyncio
async def test_postgres_search_repository_index_and_search(session_maker, test_project):
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)
    await repo.init_search_index()  # no-op but should be exercised

    now = datetime.now(timezone.utc)
    row = SearchIndexRow(
        project_id=test_project.id,
        id=1,
        title="Coffee Brewing",
        content_stems="coffee brewing pour over",
        content_snippet="coffee brewing snippet",
        permalink="docs/coffee-brewing",
        file_path="docs/coffee-brewing.md",
        type="entity",
        metadata={"note_type": "note"},
        created_at=now,
        updated_at=now,
    )
    await repo.index_item(row)

    # Basic full-text search
    results = await repo.search(search_text="coffee")
    assert any(r.permalink == "docs/coffee-brewing" for r in results)

    # Boolean query path
    results = await repo.search(search_text="coffee AND brewing")
    assert any(r.permalink == "docs/coffee-brewing" for r in results)

    # Title-only search path
    results = await repo.search(title="Coffee Brewing")
    assert any(r.permalink == "docs/coffee-brewing" for r in results)

    # Exact permalink search
    results = await repo.search(permalink="docs/coffee-brewing")
    assert len(results) == 1

    # Permalink pattern match (LIKE)
    results = await repo.search(permalink_match="docs/coffee*")
    assert any(r.permalink == "docs/coffee-brewing" for r in results)

    # Item type filter
    results = await repo.search(search_item_types=[SearchItemType.ENTITY])
    assert any(r.permalink == "docs/coffee-brewing" for r in results)

    # Note type filter via metadata JSONB containment
    results = await repo.search(note_types=["note"])
    assert any(r.permalink == "docs/coffee-brewing" for r in results)

    # Date filter (also exercises order_by_clause)
    results = await repo.search(after_date=now - timedelta(days=1))
    assert any(r.permalink == "docs/coffee-brewing" for r in results)

    # Limit/offset
    results = await repo.search(limit=1, offset=0)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_postgres_search_repository_bulk_index_items_and_prepare_terms(
    session_maker, test_project
):
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)

    # Empty batch is a no-op
    await repo.bulk_index_items([])

    # Exercise term preparation helpers
    assert "&" in repo._prepare_search_term("coffee AND brewing")
    assert repo._prepare_search_term("coff*") == "coff:*"
    assert repo._prepare_search_term("()&!:") == "NOSPECIALCHARS:*"
    assert repo._prepare_search_term("coffee brewing") == "coffee:* & brewing:*"
    assert repo._prepare_single_term("   ") == "   "
    assert repo._prepare_single_term("coffee", is_prefix=False) == "coffee"

    now = datetime.now(timezone.utc)
    rows = [
        SearchIndexRow(
            project_id=test_project.id,
            id=10,
            title="Pour Over",
            content_stems="pour over coffee",
            content_snippet="pour over snippet",
            permalink="docs/pour-over",
            file_path="docs/pour-over.md",
            type="entity",
            metadata={"note_type": "note"},
            created_at=now,
            updated_at=now,
        ),
        SearchIndexRow(
            project_id=test_project.id,
            id=11,
            title="French Press",
            content_stems="french press coffee",
            content_snippet="french press snippet",
            permalink="docs/french-press",
            file_path="docs/french-press.md",
            type="entity",
            metadata={"note_type": "note"},
            created_at=now,
            updated_at=now,
        ),
    ]

    await repo.bulk_index_items(rows)

    results = await repo.search(search_text="coffee")
    permalinks = {r.permalink for r in results}
    assert "docs/pour-over" in permalinks
    assert "docs/french-press" in permalinks


@pytest.mark.asyncio
async def test_postgres_search_repository_wildcard_text_and_permalink_match_exact(
    session_maker, test_project
):
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)

    now = datetime.now(timezone.utc)
    await repo.index_item(
        SearchIndexRow(
            project_id=test_project.id,
            id=1,
            title="X",
            content_stems="x",
            content_snippet="x",
            permalink="docs/x",
            file_path="docs/x.md",
            type="entity",
            metadata={"note_type": "note"},
            created_at=now,
            updated_at=now,
        )
    )

    # search_text="*" should not add tsquery conditions (covers the pass branch)
    results = await repo.search(search_text="*")
    assert results

    # permalink_match without '*' uses exact match branch
    results = await repo.search(permalink_match="docs/x")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_postgres_search_repository_tsquery_syntax_error_returns_empty(
    session_maker, test_project
):
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)

    # Trailing boolean operator creates an invalid tsquery; repository should return []
    results = await repo.search(search_text="coffee AND")
    assert results == []


@pytest.mark.asyncio
async def test_postgres_search_repository_reraises_non_tsquery_db_errors(
    session_maker, test_project
):
    """Dropping the search_index table triggers a non-tsquery DB error which should be re-raised."""
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)

    from sqlalchemy import text
    from basic_memory import db

    async with db.scoped_session(session_maker) as session:
        await session.execute(text("DROP TABLE search_index"))
        await session.commit()

    with pytest.raises(Exception):
        # Use a non-text query so the generated SQL doesn't include to_tsquery(),
        # ensuring we hit the generic "re-raise other db errors" branch.
        await repo.search(permalink="docs/anything")


@pytest.mark.asyncio
async def test_bulk_index_items_strips_nul_bytes(session_maker, test_project):
    """NUL bytes in content must not cause CharacterNotInRepertoireError on INSERT."""
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)
    now = datetime.now(timezone.utc)
    row = SearchIndexRow(
        project_id=test_project.id,
        id=99,
        title="hello\x00world",
        content_stems="some\x00stems",
        content_snippet="snippet\x00here",
        permalink="test/nul-row",
        file_path="test/nul.md",
        type="entity",
        metadata={"note_type": "note"},
        created_at=now,
        updated_at=now,
    )
    # Should not raise CharacterNotInRepertoireError
    await repo.bulk_index_items([row])
    results = await repo.search(permalink="test/nul-row")
    assert len(results) == 1
    assert "\x00" not in (results[0].content_snippet or "")
    assert "\x00" not in (results[0].title or "")


@pytest.mark.asyncio
async def test_index_item_strips_nul_bytes(session_maker, test_project):
    """NUL bytes in single-item index_item path must not cause CharacterNotInRepertoireError."""
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)
    now = datetime.now(timezone.utc)
    row = SearchIndexRow(
        project_id=test_project.id,
        id=98,
        title="single\x00item",
        content_stems="nul\x00stems",
        content_snippet="nul\x00snippet",
        permalink="test/nul-single",
        file_path="test/nul-single.md",
        type="entity",
        metadata={"note_type": "note"},
        created_at=now,
        updated_at=now,
    )
    await repo.index_item(row)
    results = await repo.search(permalink="test/nul-single")
    assert len(results) == 1
    assert "\x00" not in (results[0].content_snippet or "")
    assert "\x00" not in (results[0].title or "")


def test_strip_nul_from_row():
    """_strip_nul_from_row strips NUL bytes from string values, leaves non-strings alone."""
    row = {
        "title": "hello\x00world",
        "content_stems": "some\x00content\x00here",
        "content_snippet": "clean",
        "id": 42,
        "metadata": None,
        "created_at": datetime(2024, 1, 1),
    }
    result = _strip_nul_from_row(row)
    assert result["title"] == "helloworld"
    assert result["content_stems"] == "somecontenthere"
    assert result["content_snippet"] == "clean"
    assert result["id"] == 42
    assert result["metadata"] is None
    assert result["created_at"] == datetime(2024, 1, 1)


@pytest.mark.asyncio
async def test_postgres_semantic_vector_search_returns_ranked_entities(session_maker, test_project):
    """Vector mode ranks entities via pgvector distance."""
    await _skip_if_pgvector_unavailable(session_maker)
    app_config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        database_backend=DatabaseBackend.POSTGRES,
        semantic_search_enabled=True,
    )
    repo = PostgresSearchRepository(
        session_maker,
        project_id=test_project.id,
        app_config=app_config,
        embedding_provider=StubEmbeddingProvider(),
    )
    await repo.init_search_index()

    now = datetime.now(timezone.utc)
    await repo.bulk_index_items(
        [
            SearchIndexRow(
                project_id=test_project.id,
                id=401,
                title="Authentication Decisions",
                content_stems="login session token refresh auth design",
                content_snippet="auth snippet",
                permalink="specs/authentication",
                file_path="specs/authentication.md",
                type=SearchItemType.ENTITY.value,
                entity_id=401,
                metadata={"note_type": "spec"},
                created_at=now,
                updated_at=now,
            ),
            SearchIndexRow(
                project_id=test_project.id,
                id=402,
                title="Database Migrations",
                content_stems="alembic sqlite postgres schema migration ddl",
                content_snippet="db snippet",
                permalink="specs/migrations",
                file_path="specs/migrations.md",
                type=SearchItemType.ENTITY.value,
                entity_id=402,
                metadata={"note_type": "spec"},
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    await repo.sync_entity_vectors(401)
    await repo.sync_entity_vectors(402)

    results = await repo.search(
        search_text="session token auth",
        retrieval_mode=SearchRetrievalMode.VECTOR,
        limit=5,
        offset=0,
    )

    assert results
    assert results[0].permalink == "specs/authentication"
    assert all(result.type == SearchItemType.ENTITY.value for result in results)


@pytest.mark.asyncio
async def test_postgres_semantic_hybrid_search_combines_fts_and_vector(session_maker, test_project):
    """Hybrid mode fuses FTS and vector ranks using RRF."""
    await _skip_if_pgvector_unavailable(session_maker)
    app_config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        database_backend=DatabaseBackend.POSTGRES,
        semantic_search_enabled=True,
    )
    repo = PostgresSearchRepository(
        session_maker,
        project_id=test_project.id,
        app_config=app_config,
        embedding_provider=StubEmbeddingProvider(),
    )

    now = datetime.now(timezone.utc)
    await repo.bulk_index_items(
        [
            SearchIndexRow(
                project_id=test_project.id,
                id=411,
                title="Task Queue Worker",
                content_stems="queue worker retries async processing",
                content_snippet="worker snippet",
                permalink="specs/task-queue-worker",
                file_path="specs/task-queue-worker.md",
                type=SearchItemType.ENTITY.value,
                entity_id=411,
                metadata={"note_type": "spec"},
                created_at=now,
                updated_at=now,
            ),
            SearchIndexRow(
                project_id=test_project.id,
                id=412,
                title="Search Index Notes",
                content_stems="fts bm25 ranking vector search hybrid rrf",
                content_snippet="search snippet",
                permalink="specs/search-index",
                file_path="specs/search-index.md",
                type=SearchItemType.ENTITY.value,
                entity_id=412,
                metadata={"note_type": "spec"},
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    await repo.sync_entity_vectors(411)
    await repo.sync_entity_vectors(412)

    results = await repo.search(
        search_text="hybrid vector search",
        retrieval_mode=SearchRetrievalMode.HYBRID,
        limit=5,
        offset=0,
    )

    assert results
    assert any(result.permalink == "specs/search-index" for result in results)


@pytest.mark.asyncio
async def test_postgres_vector_mode_rejects_non_text_query(session_maker, test_project):
    """Vector mode should fail fast for title-only queries."""
    app_config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        database_backend=DatabaseBackend.POSTGRES,
        semantic_search_enabled=True,
    )
    repo = PostgresSearchRepository(
        session_maker,
        project_id=test_project.id,
        app_config=app_config,
        embedding_provider=StubEmbeddingProvider(),
    )

    with pytest.raises(ValueError):
        await repo.search(
            title="Authentication Decisions",
            retrieval_mode=SearchRetrievalMode.VECTOR,
            search_item_types=[SearchItemType.ENTITY],
        )


@pytest.mark.asyncio
async def test_postgres_vector_mode_fails_when_semantic_disabled(session_maker, test_project):
    """Vector mode should fail fast when semantic search is disabled."""
    app_config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        database_backend=DatabaseBackend.POSTGRES,
        semantic_search_enabled=False,
    )
    repo = PostgresSearchRepository(
        session_maker,
        project_id=test_project.id,
        app_config=app_config,
        embedding_provider=StubEmbeddingProvider(),
    )

    with pytest.raises(SemanticSearchDisabledError):
        await repo.search(
            search_text="auth session",
            retrieval_mode=SearchRetrievalMode.VECTOR,
        )


class StubEmbeddingProvider8d:
    """Embedding provider with 8 dimensions to test dimension mismatch detection."""

    model_name = "stub-8d"
    dimensions = 8

    async def embed_query(self, text: str) -> list[float]:
        return [0.0] * 8

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 8 for _ in texts]


@pytest.mark.asyncio
async def test_postgres_dimension_mismatch_triggers_table_recreation(session_maker, test_project):
    """Changing embedding dimensions should drop and recreate the embeddings table."""
    await _skip_if_pgvector_unavailable(session_maker)

    # --- First, create tables with 4 dimensions ---
    app_config_4d = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        database_backend=DatabaseBackend.POSTGRES,
        semantic_search_enabled=True,
    )
    repo_4d = PostgresSearchRepository(
        session_maker,
        project_id=test_project.id,
        app_config=app_config_4d,
        embedding_provider=StubEmbeddingProvider(),
    )
    await repo_4d._ensure_vector_tables()

    # Verify table exists with 4 dimensions
    async with db.scoped_session(session_maker) as session:
        result = await session.execute(
            text(
                """
                SELECT atttypmod
                FROM pg_attribute
                WHERE attrelid = 'search_vector_embeddings'::regclass
                  AND attname = 'embedding'
                """
            )
        )
        row = result.fetchone()
        assert row is not None
        assert int(row[0]) == 4

    # --- Now create a repo with 8 dimensions; should detect mismatch and recreate ---
    app_config_8d = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/basic-memory-test"},
        default_project="test-project",
        database_backend=DatabaseBackend.POSTGRES,
        semantic_search_enabled=True,
    )
    repo_8d = PostgresSearchRepository(
        session_maker,
        project_id=test_project.id,
        app_config=app_config_8d,
        embedding_provider=StubEmbeddingProvider8d(),
    )
    await repo_8d._ensure_vector_tables()

    # Verify table was recreated with 8 dimensions
    async with db.scoped_session(session_maker) as session:
        result = await session.execute(
            text(
                """
                SELECT atttypmod
                FROM pg_attribute
                WHERE attrelid = 'search_vector_embeddings'::regclass
                  AND attname = 'embedding'
                """
            )
        )
        row = result.fetchone()
        assert row is not None
        assert int(row[0]) == 8
