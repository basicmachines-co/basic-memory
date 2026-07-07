"""Integration tests for PostgresSearchRepository.

These tests only run in Postgres mode (testcontainers) and ensure that the
Postgres tsvector-backed search implementation remains well covered.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
import basic_memory.repository.search_repository_base as search_repository_base_module
from basic_memory.repository.litellm_provider import LiteLLMEmbeddingProvider
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

    def runtime_log_attrs(self) -> dict[str, object]:
        return {}

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


class StubEmbeddingProviderV2(StubEmbeddingProvider):
    """Same vectors, different model identity to force Postgres resync."""

    model_name = "stub-v2"


class StubLiteLLMEmbeddingProvider(LiteLLMEmbeddingProvider):
    """LiteLLM-shaped provider with deterministic vectors and no network calls."""

    def __init__(
        self,
        *,
        document_input_type: str,
        query_input_type: str,
    ) -> None:
        super().__init__(
            model_name="nvidia_nim/nvidia/embed-qa-4",
            dimensions=4,
            batch_size=2,
            document_input_type=document_input_type,
            query_input_type=query_input_type,
        )

    async def embed_query(self, text: str) -> list[float]:
        return StubEmbeddingProvider._vectorize(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [StubEmbeddingProvider._vectorize(text) for text in texts]


def _oversized_entity_content(bullet_count: int) -> str:
    """Build deterministic content that produces many vector chunks."""
    lines = ["# Oversized Entity"]
    lines.extend(f"- embedding job {index}" for index in range(1, bullet_count + 1))
    return "\n".join(lines)


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
    assert await repo.count(search_text="coffee AND") == 0


@pytest.mark.asyncio
async def test_postgres_search_tsquery_error_does_not_poison_caller_session(
    session_maker, test_project
):
    """A tsquery syntax error on a caller-owned session must not abort its transaction.

    The search() session param lets callers run FTS inside their own transaction
    (e.g. LinkResolver during context building). Without a SAVEPOINT, a tsquery
    syntax error aborts the shared Postgres transaction and every later query on
    that session fails with "current transaction is aborted". This exercises the
    real begin_nested() isolation on a caller-owned session.
    """
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)

    now = datetime.now(timezone.utc)
    await repo.index_item(
        SearchIndexRow(
            project_id=test_project.id,
            id=1,
            title="Coffee Brewing",
            content_stems="coffee brewing",
            content_snippet="coffee brewing snippet",
            permalink="docs/coffee-brewing",
            file_path="docs/coffee-brewing.md",
            type="entity",
            metadata={"note_type": "note"},
            created_at=now,
            updated_at=now,
        )
    )

    async with db.scoped_session(session_maker) as session:
        # Establish an active transaction the caller expects to keep using.
        pre = await session.execute(text("SELECT 1"))
        assert pre.scalar_one() == 1

        # Trailing boolean operator produces an invalid tsquery; this used to abort
        # the caller's transaction. With the savepoint it returns [] cleanly.
        results = await repo.search(search_text="coffee AND", session=session)
        assert results == []

        # Proof the caller-owned transaction survived: a normal query still works,
        # and a valid FTS query on the same session returns the indexed row.
        post = await session.execute(text("SELECT 1"))
        assert post.scalar_one() == 1

        recovered = await repo.search(search_text="coffee", session=session)
        assert any(r.permalink == "docs/coffee-brewing" for r in recovered)


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
    """Hybrid mode fuses FTS and vector results with score-based fusion."""
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
async def test_postgres_vector_sync_skips_unchanged_and_reembeds_changed_content(
    session_maker, test_project
):
    """Postgres vector sync tracks new, changed, unchanged, and model-changed entities."""
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
    await repo.index_item(
        SearchIndexRow(
            project_id=test_project.id,
            id=421,
            title="Auth and Schema Notes",
            content_stems="# Overview\n- auth token rotation\n- schema migration planning",
            content_snippet="# Overview\n- auth token rotation\n- schema migration planning",
            permalink="specs/auth-and-schema",
            file_path="specs/auth-and-schema.md",
            type=SearchItemType.ENTITY.value,
            entity_id=421,
            metadata={"note_type": "spec"},
            created_at=now,
            updated_at=now,
        )
    )

    new_result = await repo.sync_entity_vectors_batch([421])
    assert new_result.entities_synced == 1
    assert new_result.entities_skipped == 0
    assert new_result.chunks_total >= 2
    assert new_result.chunks_skipped == 0
    assert new_result.embedding_jobs_total == new_result.chunks_total

    async with db.scoped_session(session_maker) as session:
        stored_rows = await session.execute(
            text(
                "SELECT entity_fingerprint, embedding_model "
                "FROM search_vector_chunks "
                "WHERE project_id = :project_id AND entity_id = :entity_id"
            ),
            {"project_id": test_project.id, "entity_id": 421},
        )
        metadata_rows = stored_rows.fetchall()
        assert metadata_rows
        assert len({row.entity_fingerprint for row in metadata_rows}) == 1
        assert len({row.embedding_model for row in metadata_rows}) == 1
        assert metadata_rows[0].embedding_model == "StubEmbeddingProvider:stub:4"

    unchanged_result = await repo.sync_entity_vectors_batch([421])
    assert unchanged_result.entities_synced == 1
    assert unchanged_result.entities_skipped == 1
    assert unchanged_result.embedding_jobs_total == 0
    assert unchanged_result.queue_wait_seconds_total == pytest.approx(0.0, abs=0.01)
    assert unchanged_result.chunks_skipped == unchanged_result.chunks_total

    await repo.index_item(
        SearchIndexRow(
            project_id=test_project.id,
            id=421,
            title="Auth and Schema Notes",
            content_stems="# Overview\n- auth token rotation\n- database schema migration planning",
            content_snippet="# Overview\n- auth token rotation\n- database schema migration planning",
            permalink="specs/auth-and-schema",
            file_path="specs/auth-and-schema.md",
            type=SearchItemType.ENTITY.value,
            entity_id=421,
            metadata={"note_type": "spec"},
            created_at=now,
            updated_at=now,
        )
    )
    changed_result = await repo.sync_entity_vectors_batch([421])
    assert changed_result.entities_synced == 1
    assert changed_result.entities_skipped == 0
    assert changed_result.embedding_jobs_total >= 1
    assert changed_result.chunks_skipped >= 1
    assert changed_result.embedding_jobs_total < changed_result.chunks_total

    repo_v2 = PostgresSearchRepository(
        session_maker,
        project_id=test_project.id,
        app_config=app_config,
        embedding_provider=StubEmbeddingProviderV2(),
    )
    await repo_v2.init_search_index()
    model_changed_result = await repo_v2.sync_entity_vectors_batch([421])
    assert model_changed_result.entities_synced == 1
    assert model_changed_result.entities_skipped == 0
    assert model_changed_result.chunks_skipped == 0
    assert model_changed_result.embedding_jobs_total == model_changed_result.chunks_total


@pytest.mark.asyncio
async def test_postgres_litellm_role_change_reembeds_existing_chunks(session_maker, test_project):
    """LiteLLM role changes must invalidate existing Postgres vector chunks."""
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
        embedding_provider=StubLiteLLMEmbeddingProvider(
            document_input_type="passage",
            query_input_type="query",
        ),
    )
    await repo.init_search_index()

    now = datetime.now(timezone.utc)
    content = "# Retrieval Roles\n- auth token rotation\n- database schema migration planning"
    await repo.index_item(
        SearchIndexRow(
            project_id=test_project.id,
            id=431,
            title="LiteLLM Retrieval Roles",
            content_stems=content,
            content_snippet=content,
            permalink="specs/litellm-retrieval-roles",
            file_path="specs/litellm-retrieval-roles.md",
            type=SearchItemType.ENTITY.value,
            entity_id=431,
            metadata={"note_type": "spec"},
            created_at=now,
            updated_at=now,
        )
    )

    initial_result = await repo.sync_entity_vectors_batch([431])
    assert initial_result.entities_synced == 1
    assert initial_result.entities_skipped == 0
    assert initial_result.chunks_total >= 2
    assert initial_result.chunks_skipped == 0
    assert initial_result.embedding_jobs_total == initial_result.chunks_total

    unchanged_result = await repo.sync_entity_vectors_batch([431])
    assert unchanged_result.entities_synced == 1
    assert unchanged_result.entities_skipped == 1
    assert unchanged_result.embedding_jobs_total == 0
    assert unchanged_result.chunks_skipped == unchanged_result.chunks_total

    role_changed_repo = PostgresSearchRepository(
        session_maker,
        project_id=test_project.id,
        app_config=app_config,
        embedding_provider=StubLiteLLMEmbeddingProvider(
            document_input_type="document",
            query_input_type="query",
        ),
    )
    await role_changed_repo.init_search_index()

    role_changed_result = await role_changed_repo.sync_entity_vectors_batch([431])
    assert role_changed_result.entities_synced == 1
    assert role_changed_result.entities_skipped == 0
    assert role_changed_result.chunks_skipped == 0
    assert role_changed_result.embedding_jobs_total == role_changed_result.chunks_total

    async with db.scoped_session(session_maker) as session:
        stored_rows = await session.execute(
            text(
                "SELECT DISTINCT embedding_model "
                "FROM search_vector_chunks "
                "WHERE project_id = :project_id AND entity_id = :entity_id"
            ),
            {"project_id": test_project.id, "entity_id": 431},
        )
        embedding_models = {row.embedding_model for row in stored_rows.fetchall()}

    assert embedding_models == {
        "StubLiteLLMEmbeddingProvider:"
        "nvidia_nim/nvidia/embed-qa-4:4:"
        "document_input_type=document:"
        "query_input_type=query:"
        "forward_dimensions=false"
    }


@pytest.mark.asyncio
async def test_postgres_vector_sync_shards_oversized_entity_and_resumes(
    session_maker, test_project, monkeypatch
):
    """Oversized entities should sync one deterministic shard per run and resume cleanly."""
    await _skip_if_pgvector_unavailable(session_maker)
    monkeypatch.setattr(search_repository_base_module, "OVERSIZED_ENTITY_VECTOR_SHARD_SIZE", 2)

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
    content = _oversized_entity_content(5)
    await repo.index_item(
        SearchIndexRow(
            project_id=test_project.id,
            id=430,
            title="Oversized Vector Entity",
            content_stems=content,
            content_snippet=content,
            permalink="specs/oversized-vector-entity",
            file_path="specs/oversized-vector-entity.md",
            type=SearchItemType.ENTITY.value,
            entity_id=430,
            metadata={"note_type": "spec"},
            created_at=now,
            updated_at=now,
        )
    )

    first_result = await repo.sync_entity_vectors_batch([430])
    assert first_result.entities_synced == 0
    assert first_result.entities_deferred == 1
    assert first_result.entities_failed == 0
    assert first_result.embedding_jobs_total == 2
    assert first_result.chunks_total == 6
    assert first_result.chunks_skipped == 0

    second_result = await repo.sync_entity_vectors_batch([430])
    assert second_result.entities_synced == 0
    assert second_result.entities_deferred == 1
    assert second_result.entities_failed == 0
    assert second_result.embedding_jobs_total == 2
    assert second_result.chunks_total == 6
    assert second_result.chunks_skipped == 2

    third_result = await repo.sync_entity_vectors_batch([430])
    assert third_result.entities_synced == 1
    assert third_result.entities_deferred == 0
    assert third_result.entities_failed == 0
    assert third_result.embedding_jobs_total == 2
    assert third_result.chunks_total == 6
    assert third_result.chunks_skipped == 4

    unchanged_result = await repo.sync_entity_vectors_batch([430])
    assert unchanged_result.entities_synced == 1
    assert unchanged_result.entities_deferred == 0
    assert unchanged_result.entities_skipped == 1
    assert unchanged_result.embedding_jobs_total == 0
    assert unchanged_result.chunks_skipped == unchanged_result.chunks_total == 6

    async with db.scoped_session(session_maker) as session:
        chunk_count = await session.execute(
            text(
                "SELECT COUNT(*) FROM search_vector_chunks "
                "WHERE project_id = :project_id AND entity_id = :entity_id"
            ),
            {"project_id": test_project.id, "entity_id": 430},
        )
        embedding_count = await session.execute(
            text(
                "SELECT COUNT(*) FROM search_vector_embeddings e "
                "JOIN search_vector_chunks c ON c.id = e.chunk_id "
                "WHERE c.project_id = :project_id AND c.entity_id = :entity_id"
            ),
            {"project_id": test_project.id, "entity_id": 430},
        )
        assert int(chunk_count.scalar_one()) == 6
        assert int(embedding_count.scalar_one()) == 6


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

    def runtime_log_attrs(self) -> dict[str, object]:
        return {}


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


@pytest.mark.asyncio
async def test_postgres_note_types_sql_injection_returns_empty(session_maker, test_project):
    """Postgres JSONB containment with SQL injection payload must not alter query."""
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)

    malicious_payloads = [
        "note\"}}' OR '1'='1",
        'note"; DROP TABLE search_index;--',
        'note"}} UNION SELECT * FROM entity--',
    ]
    for payload in malicious_payloads:
        results = await repo.search(note_types=[payload])
        assert results == [], f"Injection payload should not match: {payload}"


@pytest.mark.asyncio
async def test_postgres_metadata_filters_path_parameterized(session_maker, test_project):
    """Metadata filter paths use jsonb_extract_path_text with parameterized parts."""
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)

    # Nested path should work without SQL injection risk
    results = await repo.search(metadata_filters={"schema.confidence": {"$gt": 0.5}})
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_postgres_search_categories_exact_match(session_maker, test_project):
    """categories filter matches the observation category exactly (mirror of #430).

    A [decision] observation that merely mentions "requirement" must be excluded
    when categories=["requirement"] is requested.
    """
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)
    now = datetime.now(timezone.utc)

    await repo.bulk_index_items(
        [
            SearchIndexRow(
                project_id=test_project.id,
                id=70101,
                type=SearchItemType.OBSERVATION.value,
                content_stems="the auth requirement must be enforced on every call",
                content_snippet="the auth requirement must be enforced on every call",
                permalink="test/obs/requirement/70101",
                file_path="test/obs.md",
                entity_id=1,
                category="requirement",
                metadata={"note_type": "note"},
                created_at=now,
                updated_at=now,
            ),
            SearchIndexRow(
                project_id=test_project.id,
                id=70102,
                type=SearchItemType.OBSERVATION.value,
                content_stems="we deferred the auth requirement to next sprint",
                content_snippet="we deferred the auth requirement to next sprint",
                permalink="test/obs/decision/70102",
                file_path="test/obs.md",
                entity_id=1,
                category="decision",
                metadata={"note_type": "note"},
                created_at=now,
                updated_at=now,
            ),
        ]
    )

    # Without the category filter, a text search for "requirement" matches both.
    text_results = await repo.search(
        search_text="requirement",
        search_item_types=[SearchItemType.OBSERVATION],
    )
    assert {r.id for r in text_results} == {70101, 70102}

    # With categories=["requirement"], only the requirement observation survives.
    filtered = await repo.search(
        search_text="requirement",
        search_item_types=[SearchItemType.OBSERVATION],
        categories=["requirement"],
    )
    assert {r.id for r in filtered} == {70101}
    assert filtered[0].category == "requirement"

    # Standalone filter and count both honor the exact category.
    filtered_only = await repo.search(categories=["requirement"])
    assert {r.id for r in filtered_only} == {70101}
    assert await repo.count(categories=["requirement"]) == 1

    # Multiple categories union.
    multi = await repo.search(categories=["requirement", "decision"])
    assert {r.id for r in multi} == {70101, 70102}


@pytest.mark.asyncio
async def test_postgres_question_punctuation_and_relaxation(session_maker, test_project):
    """Question-form queries must produce clean lexemes and a usable relaxation.

    Parity with SQLite: sentence punctuation previously reached tsquery terms,
    and a strict all-AND miss had no relaxed retry, silently disabling the FTS
    half of hybrid search for natural-language questions.
    """
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)

    # Edge punctuation stripped before lexeme formatting.
    prepared = repo._prepare_search_term("When did Melanie paint a sunrise?")
    assert "?" not in prepared
    assert "sunrise:*" in prepared

    # Relaxation drops stopwords and OR-joins content terms.
    relaxed = repo._relaxed_tsquery_text("When did Melanie paint a sunrise?")
    assert relaxed == "Melanie:* | paint:* | sunrise:*"

    # User intent is not second-guessed.
    assert repo._relaxed_tsquery_text("alpha AND beta") is None
    assert repo._relaxed_tsquery_text('"exact phrase"') is None
    assert repo._relaxed_tsquery_text(None) is None


@pytest.mark.asyncio
async def test_postgres_multiword_query_relaxes_on_strict_miss(session_maker, test_project):
    repo = PostgresSearchRepository(session_maker, project_id=test_project.id)
    now = datetime.now(timezone.utc)
    await repo.index_item(
        SearchIndexRow(
            project_id=test_project.id,
            id=77,
            title="Trip plans",
            content_stems="melanie painted a sunrise over the lake last year",
            content_snippet="Melanie painted a sunrise over the lake last year.",
            permalink="docs/trip-plans",
            file_path="docs/trip-plans.md",
            type="entity",
            metadata={"note_type": "note"},
            created_at=now,
            updated_at=now,
        )
    )

    # A content word absent from the doc ("hiking") makes the strict
    # all-terms-AND query miss even after Postgres drops stopwords — without
    # it, to_tsquery('english', ...) already strips "when/did/a" and matches.
    strict = await repo.search(search_text="Did Melanie go hiking at sunrise?")
    assert strict == []

    # The hybrid FTS branch opts in; OR-relaxation surfaces the partial match.
    results = await repo.search(search_text="Did Melanie go hiking at sunrise?", allow_relaxed=True)
    assert any(r.id == 77 for r in results)
