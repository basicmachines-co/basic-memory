"""Unit tests for PostgresSearchRepository pure-Python helpers.

These tests exercise methods that do not require a real Postgres connection,
covering utility functions, formatting helpers, and constructor paths that
are difficult to reach in integration tests.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import basic_memory.repository.search_repository_base as search_repository_base_module
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_repository_base import _PreparedEntityVectorSync
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
    semantic_postgres_prepare_concurrency: int = 4,
) -> PostgresSearchRepository:
    """Build a PostgresSearchRepository with a no-op session maker."""
    session_maker = MagicMock()
    app_config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/test"},
        default_project="test-project",
        database_backend=DatabaseBackend.POSTGRES,
        semantic_search_enabled=semantic_enabled,
        semantic_postgres_prepare_concurrency=semantic_postgres_prepare_concurrency,
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
    async def test_write_embeddings_executes_single_bulk_upsert(self):
        repo = _make_repo()
        session = AsyncMock()
        jobs = [(100, "chunk text A"), (200, "chunk text B")]
        embeddings = [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]]
        await repo._write_embeddings(session, jobs, embeddings)
        assert session.execute.call_count == 1
        params = session.execute.call_args[0][1]
        assert params["chunk_id_0"] == 100
        assert params["chunk_id_1"] == 200
        assert params["project_id"] == repo.project_id
        assert params["embedding_dims_0"] == 4
        assert params["embedding_dims_1"] == 4


class TestBatchPrepareConcurrency:
    """Cover the Postgres-specific concurrent prepare window."""

    @pytest.mark.asyncio
    async def test_sync_entity_vectors_batch_prepares_entities_concurrently(self, monkeypatch):
        repo = _make_repo(
            semantic_enabled=True,
            embedding_provider=StubEmbeddingProvider(),
            semantic_postgres_prepare_concurrency=2,
        )
        repo._semantic_embedding_sync_batch_size = 8
        repo._vector_tables_initialized = True

        active_prepares = 0
        max_active_prepares = 0

        async def _stub_prepare(entity_id: int) -> _PreparedEntityVectorSync:
            nonlocal active_prepares, max_active_prepares
            active_prepares += 1
            max_active_prepares = max(max_active_prepares, active_prepares)
            await asyncio.sleep(0)
            active_prepares -= 1
            return _PreparedEntityVectorSync(
                entity_id=entity_id,
                sync_start=float(entity_id),
                source_rows_count=1,
                embedding_jobs=[],
            )

        monkeypatch.setattr(repo, "_ensure_vector_tables", AsyncMock())
        monkeypatch.setattr(repo, "_prepare_entity_vector_jobs", _stub_prepare)

        result = await repo.sync_entity_vectors_batch([1, 2, 3, 4])

        assert result.entities_total == 4
        assert result.entities_synced == 4
        assert result.entities_failed == 0
        assert max_active_prepares == 2


@pytest.mark.asyncio
async def test_postgres_batch_sync_tracks_prepare_and_queue_wait(monkeypatch):
    """Postgres batch sync should separate queue wait from prepare/embed/write."""
    repo = _make_repo(
        semantic_enabled=True,
        embedding_provider=StubEmbeddingProvider(),
    )
    repo._semantic_embedding_sync_batch_size = 2
    repo._vector_tables_initialized = True

    async def _stub_prepare(entity_id: int) -> _PreparedEntityVectorSync:
        return _PreparedEntityVectorSync(
            entity_id=entity_id,
            sync_start=0.0,
            source_rows_count=1,
            embedding_jobs=[(200 + entity_id, f"chunk-{entity_id}")],
            prepare_seconds=1.0,
        )

    async def _stub_flush(flush_jobs, entity_runtime, synced_entity_ids):
        for job in flush_jobs:
            runtime = entity_runtime[job.entity_id]
            if job.entity_id == 1:
                runtime.embed_seconds = 1.0
                runtime.write_seconds = 0.5
            else:
                runtime.embed_seconds = 2.0
                runtime.write_seconds = 0.5
            runtime.remaining_jobs = 0
            synced_entity_ids.add(job.entity_id)
        return (3.0, 1.0)

    completion_records: list[dict] = []

    def _capture_log(**kwargs):
        completion_records.append(kwargs)

    perf_counter_values = iter([4.0, 5.0])

    monkeypatch.setattr(repo, "_ensure_vector_tables", AsyncMock())
    monkeypatch.setattr(repo, "_prepare_entity_vector_jobs", _stub_prepare)
    monkeypatch.setattr(repo, "_flush_embedding_jobs", _stub_flush)
    monkeypatch.setattr(repo, "_log_vector_sync_complete", _capture_log)
    monkeypatch.setattr(
        search_repository_base_module.time,
        "perf_counter",
        lambda: next(perf_counter_values),
    )

    result = await repo.sync_entity_vectors_batch([1, 2])

    assert result.entities_total == 2
    assert result.entities_synced == 2
    assert result.entities_failed == 0
    assert result.prepare_seconds_total == pytest.approx(2.0)
    assert result.queue_wait_seconds_total == pytest.approx(3.0)
    assert result.embed_seconds_total == pytest.approx(3.0)
    assert result.write_seconds_total == pytest.approx(1.0)
    assert len(completion_records) == 2
    for record in completion_records:
        assert record["prepare_seconds"] == pytest.approx(1.0)
        assert record["queue_wait_seconds"] == pytest.approx(1.5)


@pytest.mark.asyncio
async def test_postgres_batch_sync_tracks_deferred_oversized_entities(monkeypatch):
    """Oversized shard runs should be deferred until the last shard completes."""
    repo = _make_repo(
        semantic_enabled=True,
        embedding_provider=StubEmbeddingProvider(),
    )
    repo._semantic_embedding_sync_batch_size = 8
    repo._vector_tables_initialized = True

    async def _stub_prepare(entity_id: int) -> _PreparedEntityVectorSync:
        if entity_id == 1:
            return _PreparedEntityVectorSync(
                entity_id=entity_id,
                sync_start=0.0,
                source_rows_count=1,
                embedding_jobs=[(201, "chunk-1a"), (202, "chunk-1b")],
                chunks_total=5,
                pending_jobs_total=5,
                entity_complete=False,
                oversized_entity=True,
                shard_index=1,
                shard_count=3,
                remaining_jobs_after_shard=3,
            )
        return _PreparedEntityVectorSync(
            entity_id=entity_id,
            sync_start=0.0,
            source_rows_count=1,
            embedding_jobs=[(301, "chunk-2a")],
            chunks_total=1,
            pending_jobs_total=1,
            entity_complete=True,
            shard_index=1,
            shard_count=1,
            remaining_jobs_after_shard=0,
        )

    async def _stub_flush(flush_jobs, entity_runtime, synced_entity_ids):
        for job in flush_jobs:
            runtime = entity_runtime[job.entity_id]
            runtime.remaining_jobs -= 1
            runtime.embed_seconds += 0.5
            runtime.write_seconds += 0.25
        return (1.5, 0.75)

    completion_records: list[dict] = []

    def _capture_log(**kwargs):
        completion_records.append(kwargs)

    monkeypatch.setattr(repo, "_ensure_vector_tables", AsyncMock())
    monkeypatch.setattr(repo, "_prepare_entity_vector_jobs", _stub_prepare)
    monkeypatch.setattr(repo, "_flush_embedding_jobs", _stub_flush)
    monkeypatch.setattr(repo, "_log_vector_sync_complete", _capture_log)

    result = await repo.sync_entity_vectors_batch([1, 2])

    assert result.entities_total == 2
    assert result.entities_synced == 1
    assert result.entities_deferred == 1
    assert result.entities_failed == 0
    assert result.embedding_jobs_total == 3

    deferred_record = next(record for record in completion_records if record["entity_id"] == 1)
    assert deferred_record["entity_complete"] is False
    assert deferred_record["oversized_entity"] is True
    assert deferred_record["pending_jobs_total"] == 5
    assert deferred_record["shard_count"] == 3
    assert deferred_record["remaining_jobs_after_shard"] == 3

    complete_record = next(record for record in completion_records if record["entity_id"] == 2)
    assert complete_record["entity_complete"] is True
    assert complete_record["oversized_entity"] is False
