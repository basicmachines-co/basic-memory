"""Unit tests for PostgresSearchRepository pure-Python helpers.

These tests exercise methods that do not require a real Postgres connection,
covering utility functions, formatting helpers, and constructor paths that
are difficult to reach in integration tests.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.semantic_errors import (
    SemanticDependenciesMissingError,
    SemanticSearchDisabledError,
)


# --- Helpers ---------------------------------------------------------------


class StubEmbeddingProvider:
    """Deterministic stub for unit tests."""

    model_name = "stub"
    dimensions = 4

    async def embed_query(self, text: str) -> list[float]:
        return [0.0] * 4

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 4 for _ in texts]


def _make_repo(
    *,
    semantic_enabled: bool = False,
    embedding_provider=None,
) -> PostgresSearchRepository:
    """Build a PostgresSearchRepository with a no-op session maker."""
    session_maker = MagicMock()
    app_config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/test"},
        default_project="test-project",
        database_backend=DatabaseBackend.POSTGRES,
        semantic_search_enabled=semantic_enabled,
    )
    return PostgresSearchRepository(
        session_maker,
        project_id=1,
        app_config=app_config,
        embedding_provider=embedding_provider,
    )


# --- _format_pgvector_literal tests (lines 248-252) -----------------------


class TestFormatPgvectorLiteral:
    """Cover PostgresSearchRepository._format_pgvector_literal."""

    def test_empty_vector(self):
        assert PostgresSearchRepository._format_pgvector_literal([]) == "[]"

    def test_single_value(self):
        result = PostgresSearchRepository._format_pgvector_literal([1.0])
        assert result == "[1]"

    def test_multiple_values(self):
        result = PostgresSearchRepository._format_pgvector_literal([0.1, 0.2, 0.3])
        assert result.startswith("[")
        assert result.endswith("]")
        parts = result.strip("[]").split(",")
        assert len(parts) == 3

    def test_high_precision(self):
        """Verify that 12-significant-digit formatting is used."""
        result = PostgresSearchRepository._format_pgvector_literal([1.23456789012345])
        assert "1.23456789012" in result

    def test_integers_formatted_without_trailing_zeros(self):
        result = PostgresSearchRepository._format_pgvector_literal([1.0, 2.0, 3.0])
        assert result == "[1,2,3]"

    def test_negative_values(self):
        result = PostgresSearchRepository._format_pgvector_literal([-0.5, 0.5])
        assert "-0.5" in result
        assert "0.5" in result


# --- _timestamp_now_expr tests (line 500) ----------------------------------


class TestTimestampNowExpr:
    """Cover PostgresSearchRepository._timestamp_now_expr."""

    def test_returns_now(self):
        repo = _make_repo()
        assert repo._timestamp_now_expr() == "NOW()"


# --- Constructor auto-creates embedding provider (line 60) -----------------


class TestConstructorAutoProvider:
    """Cover the branch where embedding_provider is auto-created from config."""

    def test_auto_creates_embedding_provider_when_enabled(self):
        session_maker = MagicMock()
        app_config = BasicMemoryConfig(
            env="test",
            projects={"test-project": "/tmp/test"},
            default_project="test-project",
            database_backend=DatabaseBackend.POSTGRES,
            semantic_search_enabled=True,
        )
        stub = StubEmbeddingProvider()
        with patch(
            "basic_memory.repository.postgres_search_repository.create_embedding_provider",
            return_value=stub,
        ) as mock_factory:
            repo = PostgresSearchRepository(session_maker, project_id=1, app_config=app_config)
            mock_factory.assert_called_once_with(app_config)
            assert repo._embedding_provider is stub
            assert repo._vector_dimensions == stub.dimensions


# --- _ensure_vector_tables guard (lines 259-260) --------------------------


class TestEnsureVectorTablesGuard:
    """Cover _ensure_vector_tables early-exit when disabled or already done."""

    @pytest.mark.asyncio
    async def test_raises_when_semantic_disabled(self):
        repo = _make_repo(semantic_enabled=False)
        with pytest.raises(SemanticSearchDisabledError):
            await repo._ensure_vector_tables()

    @pytest.mark.asyncio
    async def test_raises_when_no_embedding_provider(self):
        # Start with semantic enabled + a stub, then remove the provider
        # to simulate the "extras not installed" state post-construction
        repo = _make_repo(
            semantic_enabled=True,
            embedding_provider=StubEmbeddingProvider(),
        )
        repo._embedding_provider = None
        with pytest.raises(SemanticDependenciesMissingError):
            await repo._ensure_vector_tables()

    @pytest.mark.asyncio
    async def test_skips_when_already_initialized(self):
        """Should short-circuit when _vector_tables_initialized is True."""
        repo = _make_repo(
            semantic_enabled=True,
            embedding_provider=StubEmbeddingProvider(),
        )
        repo._vector_tables_initialized = True
        # Should return immediately without touching DB
        await repo._ensure_vector_tables()
        assert repo._vector_tables_initialized is True


# --- _run_vector_query empty embedding (line 395-396) ----------------------


class TestRunVectorQueryEmpty:
    """Cover the empty-embedding early return in _run_vector_query."""

    @pytest.mark.asyncio
    async def test_returns_empty_for_empty_embedding(self):
        repo = _make_repo(
            semantic_enabled=True,
            embedding_provider=StubEmbeddingProvider(),
        )
        session = AsyncMock()
        result = await repo._run_vector_query(session, [], 10)
        assert result == []


# --- _delete_stale_chunks placeholder construction (lines 480-487) ---------


class TestDeleteStaleChunks:
    """Cover _delete_stale_chunks SQL placeholder construction."""

    @pytest.mark.asyncio
    async def test_delete_stale_chunks_builds_correct_params(self):
        repo = _make_repo()
        session = AsyncMock()
        stale_ids = [10, 20, 30]
        await repo._delete_stale_chunks(session, stale_ids, entity_id=5)

        session.execute.assert_called_once()
        call_args = session.execute.call_args
        params = call_args[0][1]
        assert params["stale_id_0"] == 10
        assert params["stale_id_1"] == 20
        assert params["stale_id_2"] == 30
        assert params["project_id"] == repo.project_id
        assert params["entity_id"] == 5


# --- _delete_entity_chunks (line 466) --------------------------------------


class TestDeleteEntityChunks:
    """Cover _delete_entity_chunks."""

    @pytest.mark.asyncio
    async def test_delete_entity_chunks_executes_sql(self):
        repo = _make_repo()
        session = AsyncMock()
        await repo._delete_entity_chunks(session, entity_id=42)
        session.execute.assert_called_once()
        call_args = session.execute.call_args
        params = call_args[0][1]
        assert params["project_id"] == repo.project_id
        assert params["entity_id"] == 42


# --- _write_embeddings (lines 437-439) -------------------------------------


class TestWriteEmbeddings:
    """Cover _write_embeddings upsert logic."""

    @pytest.mark.asyncio
    async def test_write_embeddings_executes_per_job(self):
        repo = _make_repo()
        session = AsyncMock()
        jobs = [(100, "chunk text A"), (200, "chunk text B")]
        embeddings = [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]]
        await repo._write_embeddings(session, jobs, embeddings)
        assert session.execute.call_count == 2
        first_params = session.execute.call_args_list[0][0][1]
        assert first_params["chunk_id"] == 100
        assert first_params["project_id"] == repo.project_id
        assert first_params["embedding_dims"] == 4
