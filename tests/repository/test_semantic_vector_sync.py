"""Focused edge-case coverage for shared semantic vector synchronization."""

from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from basic_memory.indexing.embedding_index_planning import EmbeddingIndexPlanner
from basic_memory.repository import semantic_vector_sync
from basic_memory.repository import search_repository_base as search_repository_base_module
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_repository_base import SearchRepositoryBase
from basic_memory.repository.semantic_chunking import VectorChunkRecord
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode


class _TestRepository(SearchRepositoryBase):
    """Small concrete repository whose hooks can be replaced per edge-case test."""

    _semantic_enabled = True
    _semantic_vector_k = 100
    _embedding_provider = None
    _semantic_embedding_sync_batch_size = 64
    _vector_dimensions = 4
    _vector_tables_initialized = False

    def __init__(self):
        self.session_maker = None
        self.project_id = 1

    async def init_search_index(self):
        pass

    def _prepare_search_term(self, term, is_prefix=True):
        return term

    async def search(
        self,
        search_text: str | None = None,
        permalink: str | None = None,
        permalink_match: str | None = None,
        title: str | None = None,
        note_types: list[str] | None = None,
        after_date: datetime | None = None,
        search_item_types: list[SearchItemType] | None = None,
        categories: list[str] | None = None,
        metadata_filters: dict[str, Any] | None = None,
        retrieval_mode: SearchRetrievalMode = SearchRetrievalMode.FTS,
        min_similarity: float | None = None,
        limit: int = 10,
        offset: int = 0,
        allow_relaxed: bool = False,
    ) -> list[SearchIndexRow]:
        return []

    async def _ensure_vector_tables(self):
        pass

    async def _run_vector_query(self, session, query_embedding, candidate_limit):
        return []

    async def _write_embeddings(self, session, jobs, embeddings):
        pass

    async def _delete_entity_chunks(self, session, entity_id):
        pass

    async def _delete_stale_chunks(self, session, stale_ids, entity_id):
        pass

    def _distance_to_similarity(self, distance: float) -> float:
        return 1.0 / (1.0 + max(distance, 0.0))


def _prepared_entity(
    entity_id: int = 1,
    *,
    embedding_jobs: list[tuple[int, str]] | None = None,
    entity_complete: bool = True,
) -> semantic_vector_sync.PreparedEntityVectorSync:
    return semantic_vector_sync.PreparedEntityVectorSync(
        entity_id=entity_id,
        sync_start=0.0,
        source_rows_count=1,
        embedding_jobs=embedding_jobs or [],
        entity_complete=entity_complete,
    )


def _batch_repository(
    monkeypatch: pytest.MonkeyPatch,
    prepared_window: list[semantic_vector_sync.PreparedEntityVectorSync | BaseException],
    *,
    batch_size: int = 2,
) -> _TestRepository:
    repository = _TestRepository()
    repository._semantic_embedding_sync_batch_size = batch_size
    monkeypatch.setattr(repository, "_embedding_provider", object())
    monkeypatch.setattr(repository, "_assert_semantic_available", Mock())
    monkeypatch.setattr(repository, "_ensure_vector_tables", AsyncMock())
    monkeypatch.setattr(repository, "_vector_prepare_window_size", Mock(return_value=8))
    monkeypatch.setattr(repository, "_log_vector_sync_runtime_settings", Mock())
    monkeypatch.setattr(
        repository,
        "_prepare_entity_vector_jobs_window",
        AsyncMock(return_value=prepared_window),
    )
    monkeypatch.setattr(repository, "_log_vector_sync_complete", Mock())
    monkeypatch.setattr(
        repository,
        "_flush_embedding_jobs",
        AsyncMock(return_value=(0.0, 0.0)),
    )
    monkeypatch.setattr(
        repository,
        "_finalize_completed_entity_syncs",
        Mock(return_value=0.0),
    )
    return repository


@pytest.mark.asyncio
async def test_vector_sync_handles_empty_batches_and_deferred_empty_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty_repository = _batch_repository(monkeypatch, [])

    empty_result = await semantic_vector_sync.sync_entity_vectors_internal(
        empty_repository,
        [],
        progress_callback=None,
        continue_on_error=True,
    )

    assert empty_result.entities_total == 0

    deferred_repository = _batch_repository(
        monkeypatch,
        [_prepared_entity(entity_complete=False)],
    )
    deferred_result = await semantic_vector_sync.sync_entity_vectors_internal(
        deferred_repository,
        [1],
        progress_callback=None,
        continue_on_error=True,
    )

    assert deferred_result.entities_deferred == 1
    assert deferred_result.entities_synced == 0


@pytest.mark.asyncio
async def test_vector_sync_propagates_prepare_and_threshold_flush_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_repository = _batch_repository(monkeypatch, [RuntimeError("prepare failed")])
    with pytest.raises(RuntimeError, match="prepare failed"):
        await semantic_vector_sync.sync_entity_vectors_internal(
            prepare_repository,
            [1],
            progress_callback=None,
            continue_on_error=False,
        )

    flush_repository = _batch_repository(
        monkeypatch,
        [_prepared_entity(embedding_jobs=[(10, "chunk")])],
        batch_size=1,
    )
    monkeypatch.setattr(
        flush_repository,
        "_flush_embedding_jobs",
        AsyncMock(side_effect=RuntimeError("flush failed")),
    )
    with pytest.raises(RuntimeError, match="flush failed"):
        await semantic_vector_sync.sync_entity_vectors_internal(
            flush_repository,
            [1],
            progress_callback=None,
            continue_on_error=False,
        )


@pytest.mark.asyncio
async def test_vector_sync_handles_final_flush_errors_and_orphan_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed_repository = _batch_repository(
        monkeypatch,
        [_prepared_entity(embedding_jobs=[(10, "chunk")])],
    )
    monkeypatch.setattr(
        failed_repository,
        "_flush_embedding_jobs",
        AsyncMock(side_effect=RuntimeError("final flush failed")),
    )

    failed_result = await semantic_vector_sync.sync_entity_vectors_internal(
        failed_repository,
        [1],
        progress_callback=None,
        continue_on_error=True,
    )

    assert failed_result.failed_entity_ids == [1]

    strict_repository = _batch_repository(
        monkeypatch,
        [_prepared_entity(embedding_jobs=[(10, "chunk")])],
    )
    monkeypatch.setattr(
        strict_repository,
        "_flush_embedding_jobs",
        AsyncMock(side_effect=RuntimeError("strict final flush failed")),
    )
    with pytest.raises(RuntimeError, match="strict final flush failed"):
        await semantic_vector_sync.sync_entity_vectors_internal(
            strict_repository,
            [1],
            progress_callback=None,
            continue_on_error=False,
        )

    orphan_repository = _batch_repository(
        monkeypatch,
        [_prepared_entity(embedding_jobs=[(10, "chunk")])],
        batch_size=1,
    )
    orphan_result = await semantic_vector_sync.sync_entity_vectors_internal(
        orphan_repository,
        [1],
        progress_callback=None,
        continue_on_error=True,
    )

    assert orphan_result.failed_entity_ids == [1]


def test_vector_shard_planning_and_logging_edges(monkeypatch) -> None:
    planner = EmbeddingIndexPlanner()
    empty_plan = planner.plan_entity_shard(
        [],
        shard_size=semantic_vector_sync.OVERSIZED_ENTITY_VECTOR_SHARD_SIZE,
    )
    assert empty_plan.entity_complete is True
    assert empty_plan.scheduled_chunk_keys == set()

    repository = _TestRepository()
    warning = Mock()
    monkeypatch.setattr(semantic_vector_sync.logger, "warning", warning)

    semantic_vector_sync.log_vector_shard_plan(
        repository,
        entity_id=1,
        shard_plan=empty_plan,
    )
    warning.assert_not_called()

    oversized_records: list[VectorChunkRecord] = [
        {
            "chunk_key": f"chunk-{index:03d}",
            "chunk_text": "text",
            "source_hash": "hash",
        }
        for index in range(semantic_vector_sync.OVERSIZED_ENTITY_VECTOR_SHARD_SIZE + 1)
    ]
    oversized_plan = planner.plan_entity_shard(
        [record["chunk_key"] for record in oversized_records],
        shard_size=semantic_vector_sync.OVERSIZED_ENTITY_VECTOR_SHARD_SIZE,
    )
    semantic_vector_sync.log_vector_shard_plan(
        repository,
        entity_id=1,
        shard_plan=oversized_plan,
    )

    assert oversized_plan.oversized_entity is True
    warning.assert_called_once()

    monkeypatch.setattr(search_repository_base_module, "OVERSIZED_ENTITY_VECTOR_SHARD_SIZE", 2)
    compatibility_plan = repository._plan_entity_vector_shard(oversized_records[:3])
    assert compatibility_plan.scheduled_chunk_keys == {"chunk-000", "chunk-001"}


@pytest.mark.asyncio
async def test_prepare_window_read_helpers_handle_empty_inputs() -> None:
    repository = _TestRepository()
    session = AsyncMock()

    source_rows = await semantic_vector_sync.fetch_prepare_window_source_rows(
        repository,
        session,
        [],
    )
    existing_rows = await semantic_vector_sync.fetch_prepare_window_existing_rows(
        repository,
        session,
        [],
    )

    assert source_rows == {}
    assert existing_rows == {}
    assert "LEFT JOIN search_vector_embeddings" in (
        semantic_vector_sync.prepare_window_existing_rows_sql(":entity_id_0")
    )
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_prepare_window_handles_empty_input_and_shared_read_failure(monkeypatch) -> None:
    repository = _TestRepository()
    assert await semantic_vector_sync.prepare_entity_vector_jobs_window(repository, []) == []

    session = AsyncMock()

    @asynccontextmanager
    async def scoped_session(_session_maker):
        yield session

    monkeypatch.setattr(semantic_vector_sync.db, "scoped_session", scoped_session)
    monkeypatch.setattr(
        repository,
        "_prepare_vector_session",
        AsyncMock(side_effect=RuntimeError("read failed")),
    )

    prepared = await semantic_vector_sync.prepare_entity_vector_jobs_window(
        repository,
        [1, 2],
    )

    assert len(prepared) == 2
    assert all(isinstance(result, RuntimeError) for result in prepared)


@pytest.mark.asyncio
async def test_prepare_single_entity_propagates_window_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _TestRepository()
    prepared_entity = _prepared_entity()
    monkeypatch.setattr(
        repository,
        "_prepare_entity_vector_jobs_window",
        AsyncMock(return_value=[prepared_entity]),
    )

    assert await semantic_vector_sync.prepare_entity_vector_jobs(repository, 1) is prepared_entity

    monkeypatch.setattr(
        repository,
        "_prepare_entity_vector_jobs_window",
        AsyncMock(return_value=[RuntimeError("failed")]),
    )

    with pytest.raises(RuntimeError, match="failed"):
        await semantic_vector_sync.prepare_entity_vector_jobs(repository, 1)


@pytest.mark.asyncio
async def test_prefetched_prepare_handles_empty_chunks_and_stale_rows(monkeypatch) -> None:
    session = SimpleNamespace(commit=AsyncMock())

    @asynccontextmanager
    async def scoped_session(_session_maker):
        yield session

    @asynccontextmanager
    async def write_scope():
        yield

    monkeypatch.setattr(semantic_vector_sync.db, "scoped_session", scoped_session)
    repository = _TestRepository()
    delete_entity_chunks = AsyncMock()
    monkeypatch.setattr(repository, "_prepare_entity_write_scope", write_scope)
    monkeypatch.setattr(repository, "_prepare_vector_session", AsyncMock())
    monkeypatch.setattr(repository, "_delete_entity_chunks", delete_entity_chunks)
    monkeypatch.setattr(repository, "_build_chunk_records", Mock(return_value=[]))

    empty_chunks = await semantic_vector_sync.prepare_entity_vector_jobs_prefetched(
        repository,
        entity_id=1,
        source_rows=[object()],
        existing_rows=[],
    )

    assert empty_chunks.embedding_jobs == []
    delete_entity_chunks.assert_awaited_once_with(session, 1)

    record = {
        "chunk_key": "new",
        "chunk_text": "text",
        "source_hash": "source-hash",
    }
    stale_row = semantic_vector_sync.VectorChunkState(
        id=7,
        chunk_key="old",
        source_hash="old-hash",
        entity_fingerprint="old-fingerprint",
        embedding_model="model",
        has_embedding=True,
    )
    monkeypatch.setattr(repository, "_build_chunk_records", Mock(return_value=[record]))
    monkeypatch.setattr(
        repository,
        "_build_entity_fingerprint",
        Mock(return_value="fingerprint"),
    )
    monkeypatch.setattr(repository, "_embedding_model_key", Mock(return_value="model"))
    monkeypatch.setattr(
        repository,
        "_timestamp_now_expr",
        Mock(return_value="CURRENT_TIMESTAMP"),
    )
    monkeypatch.setattr(repository, "_log_vector_shard_plan", Mock())
    delete_stale_chunks = AsyncMock()
    monkeypatch.setattr(repository, "_delete_stale_chunks", delete_stale_chunks)
    monkeypatch.setattr(
        repository,
        "_upsert_scheduled_chunk_records",
        AsyncMock(return_value=[]),
    )

    await semantic_vector_sync.prepare_entity_vector_jobs_prefetched(
        repository,
        entity_id=1,
        source_rows=[object()],
        existing_rows=[stale_row],
    )

    delete_stale_chunks.assert_awaited_once_with(session, [7], 1)


@pytest.mark.asyncio
async def test_flush_embedding_jobs_handles_empty_mismatch_and_missing_runtime(monkeypatch) -> None:
    repository = _TestRepository()
    embedding_provider = SimpleNamespace(embed_documents=AsyncMock(return_value=[]))
    monkeypatch.setattr(
        repository,
        "_embedding_provider",
        embedding_provider,
    )

    assert await semantic_vector_sync.flush_embedding_jobs(repository, [], {}, set()) == (
        0.0,
        0.0,
    )

    job = semantic_vector_sync.PendingEmbeddingJob(
        entity_id=1,
        chunk_row_id=10,
        chunk_text="chunk",
    )
    with pytest.raises(RuntimeError, match="unexpected number"):
        await semantic_vector_sync.flush_embedding_jobs(repository, [job], {}, set())

    session = SimpleNamespace(commit=AsyncMock())

    @asynccontextmanager
    async def scoped_session(_session_maker):
        yield session

    monkeypatch.setattr(semantic_vector_sync.db, "scoped_session", scoped_session)
    monkeypatch.setattr(repository, "_prepare_vector_session", AsyncMock())
    monkeypatch.setattr(repository, "_write_embeddings", AsyncMock())
    embedding_provider.embed_documents = AsyncMock(return_value=[[0.1]])

    embed_seconds, write_seconds = await semantic_vector_sync.flush_embedding_jobs(
        repository,
        [job],
        {},
        set(),
    )

    assert embed_seconds >= 0
    assert write_seconds >= 0


def test_finalize_completed_entity_syncs_defers_incomplete_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _TestRepository()
    monkeypatch.setattr(repository, "_log_vector_sync_complete", Mock())
    runtime = semantic_vector_sync.EntitySyncRuntime(
        sync_start=0.0,
        queue_start=0.0,
        source_rows_count=1,
        embedding_jobs_count=1,
        remaining_jobs=0,
        entity_complete=False,
    )
    deferred_entity_ids: set[int] = set()

    semantic_vector_sync.finalize_completed_entity_syncs(
        repository,
        entity_runtime={1: runtime},
        synced_entity_ids=set(),
        deferred_entity_ids=deferred_entity_ids,
    )

    assert deferred_entity_ids == {1}
