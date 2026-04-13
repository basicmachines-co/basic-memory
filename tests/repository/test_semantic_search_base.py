"""Tests for shared semantic search logic in SearchRepositoryBase.

Covers: _compose_row_source_text, _split_text_into_chunks, _build_chunk_records,
_search_hybrid entity_id fusion key, and SemanticSearchDisabledError in SQLite.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import basic_memory.repository.search_repository_base as search_repository_base_module
from basic_memory.repository.fastembed_provider import FastEmbedEmbeddingProvider
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_repository_base import (
    MAX_VECTOR_CHUNK_CHARS,
    SearchRepositoryBase,
    _PreparedEntityVectorSync,
)
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.repository.semantic_errors import SemanticSearchDisabledError
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode


# --- Helpers ---


def _make_row(
    *,
    row_type: str,
    title: str = "Test Title",
    permalink: str = "test/permalink",
    content_stems: str = "",
    content_snippet: str = "",
    category: str = "",
    relation_type: str = "",
    row_id: int = 1,
):
    """Create a SimpleNamespace mimicking a search_index DB row."""
    return SimpleNamespace(
        id=row_id,
        type=row_type,
        title=title,
        permalink=permalink,
        content_stems=content_stems,
        content_snippet=content_snippet,
        category=category,
        relation_type=relation_type,
    )


class _ConcreteRepo(SearchRepositoryBase):
    """Minimal concrete subclass for testing base class methods."""

    _semantic_enabled = False
    _semantic_vector_k = 100
    _embedding_provider = None
    _semantic_embedding_sync_batch_size = 64
    _vector_dimensions = 4
    _vector_tables_initialized = False

    def __init__(self):
        # Bypass parent __init__ since we don't need a real session_maker for unit tests
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
        metadata_filters: dict[str, Any] | None = None,
        retrieval_mode: SearchRetrievalMode = SearchRetrievalMode.FTS,
        min_similarity: float | None = None,
        limit: int = 10,
        offset: int = 0,
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

    async def _update_timestamp_sql(self):
        return "CURRENT_TIMESTAMP"

    def _distance_to_similarity(self, distance: float) -> float:
        return 1.0 / (1.0 + max(distance, 0.0))


# --- _compose_row_source_text ---


class TestComposeRowSourceText:
    """Verify _compose_row_source_text produces correct text for all row types."""

    def setup_method(self):
        self.repo = _ConcreteRepo()

    def test_entity_row_uses_content_snippet_not_content_stems(self):
        """Entity rows should use content_snippet (human-readable) instead of content_stems."""
        row = _make_row(
            row_type=SearchItemType.ENTITY.value,
            title="Auth Design",
            permalink="specs/auth-design",
            content_stems="auth login token session stems expanded variants",
            content_snippet="JWT authentication with session management",
        )
        result = self.repo._compose_row_source_text(row)
        assert "Auth Design" in result
        assert "specs/auth-design" in result
        assert "JWT authentication with session management" in result
        # content_stems should NOT appear
        assert "stems expanded variants" not in result

    def test_observation_row_includes_category(self):
        row = _make_row(
            row_type=SearchItemType.OBSERVATION.value,
            title="Coffee Notes",
            permalink="notes/coffee",
            category="technique",
            content_snippet="Pour over produces cleaner cups",
        )
        result = self.repo._compose_row_source_text(row)
        assert "Coffee Notes" in result
        assert "technique" in result
        assert "Pour over produces cleaner cups" in result

    def test_relation_row_includes_relation_type(self):
        row = _make_row(
            row_type=SearchItemType.RELATION.value,
            title="Brewing",
            permalink="notes/brewing",
            relation_type="relates_to",
            content_snippet="Coffee brewing method",
        )
        result = self.repo._compose_row_source_text(row)
        assert "Brewing" in result
        assert "relates_to" in result
        assert "Coffee brewing method" in result

    def test_entity_row_with_none_fields(self):
        """Null fields should be skipped, not included as empty strings."""
        row = _make_row(
            row_type=SearchItemType.ENTITY.value,
            title="Minimal",
            permalink="",
            content_snippet="",
        )
        row.permalink = None
        row.content_snippet = None
        result = self.repo._compose_row_source_text(row)
        assert result == "Minimal"


# --- _split_text_into_chunks ---


class TestSplitTextIntoChunks:
    """Verify markdown-aware text splitting."""

    def setup_method(self):
        self.repo = _ConcreteRepo()

    def test_short_text_returns_single_chunk(self):
        result = self.repo._split_text_into_chunks("Short text")
        assert result == ["Short text"]

    def test_empty_text_returns_empty(self):
        assert self.repo._split_text_into_chunks("") == []
        assert self.repo._split_text_into_chunks("   ") == []

    def test_splits_on_headers(self):
        # Make it long enough to actually split
        long_section_a = "## Section A\n" + ("A content. " * 100)
        long_section_b = "## Section B\n" + ("B content. " * 100)
        long_text = f"Intro paragraph\n\n{long_section_a}\n\n{long_section_b}"
        result = self.repo._split_text_into_chunks(long_text)
        assert len(result) >= 2

    def test_paragraph_merging_within_limit(self):
        """Paragraphs shorter than limit should be merged."""
        para1 = "First paragraph."
        para2 = "Second paragraph."
        text = f"# Header\n\n{para1}\n\n{para2}"
        result = self.repo._split_text_into_chunks(text)
        # Both paragraphs fit in one chunk
        assert len(result) == 1
        assert para1 in result[0]
        assert para2 in result[0]

    def test_long_paragraph_uses_char_window(self):
        """A single paragraph longer than MAX_VECTOR_CHUNK_CHARS uses sliding window."""
        long_para = "x" * (MAX_VECTOR_CHUNK_CHARS * 3)
        result = self.repo._split_text_into_chunks(long_para)
        assert len(result) >= 3
        for chunk in result:
            assert len(chunk) <= MAX_VECTOR_CHUNK_CHARS


# --- _build_chunk_records ---


class TestBuildChunkRecords:
    def setup_method(self):
        self.repo = _ConcreteRepo()

    def test_produces_records_with_correct_keys(self):
        rows = [
            _make_row(
                row_type=SearchItemType.ENTITY.value,
                title="Test",
                permalink="test",
                content_snippet="content",
                row_id=42,
            )
        ]
        records = self.repo._build_chunk_records(rows)
        assert len(records) >= 1
        for record in records:
            assert "chunk_key" in record
            assert "chunk_text" in record
            assert "source_hash" in record
            assert record["chunk_key"].startswith("entity:")

    def test_chunk_key_includes_row_id(self):
        rows = [
            _make_row(
                row_type=SearchItemType.OBSERVATION.value,
                content_snippet="obs content",
                row_id=99,
            )
        ]
        records = self.repo._build_chunk_records(rows)
        assert any("99" in r["chunk_key"] for r in records)

    def test_duplicate_rows_collapse_to_unique_chunk_keys(self):
        rows = [
            _make_row(
                row_type=SearchItemType.ENTITY.value,
                title="Spec",
                permalink="spec",
                content_snippet="shared content",
                row_id=77,
            ),
            _make_row(
                row_type=SearchItemType.ENTITY.value,
                title="Spec",
                permalink="spec",
                content_snippet="shared content",
                row_id=77,
            ),
        ]

        records = self.repo._build_chunk_records(rows)

        assert len(records) == 1
        assert records[0]["chunk_key"] == "entity:77:0"


# --- SQLite SemanticSearchDisabledError ---


@pytest.mark.asyncio
async def test_sqlite_vector_search_raises_disabled_error(search_repository):
    """Vector mode on SQLite should raise SemanticSearchDisabledError when disabled."""
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("SQLite-specific test.")

    search_repository._semantic_enabled = False
    with pytest.raises(SemanticSearchDisabledError):
        await search_repository.search(
            search_text="test query",
            retrieval_mode=SearchRetrievalMode.VECTOR,
            limit=5,
            offset=0,
        )


@pytest.mark.asyncio
async def test_sqlite_hybrid_search_raises_disabled_error(search_repository):
    """Hybrid mode on SQLite should raise SemanticSearchDisabledError when disabled."""
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("SQLite-specific test.")

    search_repository._semantic_enabled = False
    with pytest.raises(SemanticSearchDisabledError):
        await search_repository.search(
            search_text="test query",
            retrieval_mode=SearchRetrievalMode.HYBRID,
            limit=5,
            offset=0,
        )


@pytest.mark.asyncio
async def test_sync_entity_vectors_batch_flushes_at_configured_threshold(monkeypatch):
    """Batch sync should flush queued jobs at semantic_embedding_sync_batch_size boundaries."""
    repo = _ConcreteRepo()
    repo._semantic_enabled = True
    repo._embedding_provider = object()
    repo._semantic_embedding_sync_batch_size = 2

    prepared_by_entity = {
        1: _PreparedEntityVectorSync(1, 1.0, 1, [(101, "chunk-1")]),
        2: _PreparedEntityVectorSync(2, 2.0, 1, [(102, "chunk-2")]),
        3: _PreparedEntityVectorSync(3, 3.0, 1, [(103, "chunk-3")]),
    }
    flush_sizes: list[int] = []

    async def _stub_prepare_window(entity_ids: list[int]):
        return [prepared_by_entity[entity_id] for entity_id in entity_ids]

    async def _stub_flush(flush_jobs, entity_runtime, synced_entity_ids):
        flush_sizes.append(len(flush_jobs))
        for job in flush_jobs:
            runtime = entity_runtime[job.entity_id]
            runtime.remaining_jobs -= 1
            if runtime.remaining_jobs <= 0:
                synced_entity_ids.add(job.entity_id)
                entity_runtime.pop(job.entity_id, None)
        return (0.1, 0.2)

    monkeypatch.setattr(repo, "_prepare_entity_vector_jobs_window", _stub_prepare_window)
    monkeypatch.setattr(repo, "_flush_embedding_jobs", _stub_flush)

    result = await repo.sync_entity_vectors_batch([1, 2, 3])

    assert flush_sizes == [2, 1]
    assert result.entities_total == 3
    assert result.entities_synced == 3
    assert result.entities_failed == 0
    assert result.failed_entity_ids == []
    assert result.embedding_jobs_total == 3
    assert result.prepare_seconds_total == pytest.approx(0.0)
    assert result.queue_wait_seconds_total == pytest.approx(0.0)
    assert result.embed_seconds_total == pytest.approx(0.2)
    assert result.write_seconds_total == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_sync_entity_vectors_batch_skip_only_has_zero_queue_wait(monkeypatch):
    """Skip-only batches should not accumulate synthetic queue wait."""
    repo = _ConcreteRepo()
    repo._semantic_enabled = True
    repo._embedding_provider = object()

    async def _stub_prepare_window(entity_ids: list[int]):
        return [
            _PreparedEntityVectorSync(
                entity_id=entity_id,
                sync_start=float(entity_id),
                source_rows_count=1,
                embedding_jobs=[],
                chunks_total=2,
                chunks_skipped=2,
                entity_skipped=True,
                prepare_seconds=0.25,
            )
            for entity_id in entity_ids
        ]

    monkeypatch.setattr(repo, "_prepare_entity_vector_jobs_window", _stub_prepare_window)

    result = await repo.sync_entity_vectors_batch([1, 2])

    assert result.entities_total == 2
    assert result.entities_synced == 2
    assert result.entities_skipped == 2
    assert result.embedding_jobs_total == 0
    assert result.queue_wait_seconds_total == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_sync_entity_vectors_batch_progress_tracks_terminal_entities(monkeypatch):
    """Progress callback should advance on terminal entity completion, not prepare entry."""
    repo = _ConcreteRepo()
    repo._semantic_enabled = True
    repo._embedding_provider = object()
    repo._semantic_embedding_sync_batch_size = 2

    prepared_by_entity = {
        1: _PreparedEntityVectorSync(1, 1.0, 1, []),
        2: _PreparedEntityVectorSync(2, 2.0, 1, [(102, "chunk-2")]),
        3: _PreparedEntityVectorSync(3, 3.0, 1, [(103, "chunk-3")]),
    }
    progress_events: list[tuple[int, int, int]] = []

    async def _stub_prepare_window(entity_ids: list[int]):
        return [prepared_by_entity[entity_id] for entity_id in entity_ids]

    async def _stub_flush(flush_jobs, entity_runtime, synced_entity_ids):
        for job in flush_jobs:
            runtime = entity_runtime[job.entity_id]
            runtime.remaining_jobs -= 1
            if runtime.remaining_jobs <= 0:
                synced_entity_ids.add(job.entity_id)
        return (0.1, 0.2)

    monkeypatch.setattr(repo, "_prepare_entity_vector_jobs_window", _stub_prepare_window)
    monkeypatch.setattr(repo, "_flush_embedding_jobs", _stub_flush)

    result = await repo.sync_entity_vectors_batch(
        [1, 2, 3],
        progress_callback=lambda entity_id, completed, total: progress_events.append(
            (entity_id, completed, total)
        ),
    )

    assert result.entities_synced == 3
    assert progress_events == [
        (1, 1, 3),
        (2, 2, 3),
        (3, 3, 3),
    ]


@pytest.mark.asyncio
async def test_sync_entity_vectors_batch_continue_on_error(monkeypatch):
    """Batch sync should continue after per-entity and per-flush failures."""
    repo = _ConcreteRepo()
    repo._semantic_enabled = True
    repo._embedding_provider = object()
    repo._semantic_embedding_sync_batch_size = 1

    async def _stub_prepare_window(entity_ids: list[int]):
        prepared = []
        for entity_id in entity_ids:
            if entity_id == 2:
                prepared.append(RuntimeError("prepare failed"))
                continue
            prepared.append(
                _PreparedEntityVectorSync(
                    entity_id, float(entity_id), 1, [(100 + entity_id, "chunk")]
                )
            )
        return prepared

    async def _stub_flush(flush_jobs, entity_runtime, synced_entity_ids):
        entity_id = flush_jobs[0].entity_id
        if entity_id == 3:
            raise RuntimeError("flush failed")
        runtime = entity_runtime[entity_id]
        runtime.remaining_jobs = 0
        synced_entity_ids.add(entity_id)
        entity_runtime.pop(entity_id, None)
        return (0.05, 0.05)

    monkeypatch.setattr(repo, "_prepare_entity_vector_jobs_window", _stub_prepare_window)
    monkeypatch.setattr(repo, "_flush_embedding_jobs", _stub_flush)

    result = await repo.sync_entity_vectors_batch([1, 2, 3])

    assert result.entities_total == 3
    assert result.entities_synced == 1
    assert result.entities_failed == 2
    assert result.failed_entity_ids == [2, 3]


@pytest.mark.asyncio
async def test_sync_entity_vectors_batch_only_attributes_queue_wait_to_flushed_entities(
    monkeypatch,
):
    """Mixed batches should only charge queue wait to entities that entered flush work."""
    repo = _ConcreteRepo()
    repo._semantic_enabled = True
    repo._embedding_provider = object()
    repo._semantic_embedding_sync_batch_size = 2

    async def _stub_prepare_window(entity_ids: list[int]):
        prepared: list[_PreparedEntityVectorSync] = []
        for entity_id in entity_ids:
            if entity_id == 1:
                prepared.append(
                    _PreparedEntityVectorSync(
                        entity_id=1,
                        sync_start=0.0,
                        source_rows_count=1,
                        embedding_jobs=[],
                        chunks_total=2,
                        chunks_skipped=2,
                        entity_skipped=True,
                        prepare_seconds=0.5,
                    )
                )
                continue
            prepared.append(
                _PreparedEntityVectorSync(
                    entity_id=2,
                    sync_start=0.0,
                    source_rows_count=1,
                    embedding_jobs=[(102, "chunk-2")],
                    prepare_seconds=1.0,
                )
            )
        return prepared

    async def _stub_flush(flush_jobs, entity_runtime, synced_entity_ids):
        runtime = entity_runtime[2]
        runtime.embed_seconds = 1.0
        runtime.write_seconds = 0.5
        runtime.remaining_jobs = 0
        synced_entity_ids.add(2)
        return (1.0, 0.5)

    perf_counter_values = iter([0.0, 2.0, 4.0, 5.0])

    monkeypatch.setattr(repo, "_prepare_entity_vector_jobs_window", _stub_prepare_window)
    monkeypatch.setattr(repo, "_flush_embedding_jobs", _stub_flush)
    monkeypatch.setattr(
        search_repository_base_module.time,
        "perf_counter",
        lambda: next(perf_counter_values),
    )

    result = await repo.sync_entity_vectors_batch([1, 2])

    assert result.entities_total == 2
    assert result.entities_synced == 2
    assert result.entities_skipped == 1
    assert result.embedding_jobs_total == 1
    assert result.queue_wait_seconds_total == pytest.approx(1.5)


@pytest.mark.asyncio
async def test_sync_entity_vectors_batch_tracks_prepare_and_queue_wait_seconds(monkeypatch):
    """Queue wait should be reported separately from prepare/embed/write timings."""
    repo = _ConcreteRepo()
    repo._semantic_enabled = True
    repo._embedding_provider = object()
    repo._semantic_embedding_sync_batch_size = 2

    async def _stub_prepare_window(entity_ids: list[int]):
        return [
            _PreparedEntityVectorSync(
                entity_id=entity_id,
                sync_start=0.0,
                source_rows_count=1,
                embedding_jobs=[(100 + entity_id, f"chunk-{entity_id}")],
                prepare_seconds=1.0,
            )
            for entity_id in entity_ids
        ]

    async def _stub_flush(flush_jobs, entity_runtime, synced_entity_ids):
        assert len(flush_jobs) == 2
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

    logged_completion: list[dict] = []

    def _capture_log(**kwargs):
        logged_completion.append(kwargs)

    perf_counter_values = iter([0.0, 4.0, 5.0, 6.0])

    monkeypatch.setattr(repo, "_prepare_entity_vector_jobs_window", _stub_prepare_window)
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
    assert len(logged_completion) == 2
    for record in logged_completion:
        assert record["prepare_seconds"] == pytest.approx(1.0)
        assert record["queue_wait_seconds"] == pytest.approx(1.5)


@pytest.mark.asyncio
async def test_prepare_window_uses_entity_local_timing_after_shared_reads(monkeypatch):
    """Per-entity prepare timing should start when that entity work actually begins."""
    repo = _ConcreteRepo()
    repo._semantic_enabled = True
    repo._embedding_provider = SimpleNamespace(model_name="stub", dimensions=4)

    async def _stub_fetch_source_rows(session, entity_ids: list[int]):
        search_repository_base_module.time.perf_counter()
        return {entity_id: [] for entity_id in entity_ids}

    async def _stub_fetch_existing_rows(session, entity_ids: list[int]):
        search_repository_base_module.time.perf_counter()
        return {entity_id: [] for entity_id in entity_ids}

    @asynccontextmanager
    async def fake_scoped_session(session_maker):
        yield AsyncMock()

    @asynccontextmanager
    async def _yielding_write_scope():
        await asyncio.sleep(0)
        yield

    perf_counter_values = iter([0.0, 5.0, 10.0, 11.0, 12.0, 13.0])

    monkeypatch.setattr(
        "basic_memory.repository.search_repository_base.db.scoped_session",
        fake_scoped_session,
    )
    monkeypatch.setattr(repo, "_fetch_prepare_window_source_rows", _stub_fetch_source_rows)
    monkeypatch.setattr(repo, "_fetch_prepare_window_existing_rows", _stub_fetch_existing_rows)
    monkeypatch.setattr(repo, "_prepare_entity_write_scope", _yielding_write_scope)
    monkeypatch.setattr(repo, "_prepare_vector_session", AsyncMock())
    monkeypatch.setattr(repo, "_delete_entity_chunks", AsyncMock())
    monkeypatch.setattr(
        search_repository_base_module.time,
        "perf_counter",
        lambda: next(perf_counter_values),
    )

    prepared = await repo._prepare_entity_vector_jobs_window([1, 2])
    prepared_results = [
        result for result in prepared if isinstance(result, _PreparedEntityVectorSync)
    ]

    assert len(prepared_results) == 2
    assert [result.sync_start for result in prepared_results] == [10.0, 11.0]
    assert [result.prepare_seconds for result in prepared_results] == [2.0, 2.0]


@pytest.mark.asyncio
async def test_sync_entity_vectors_batch_records_entity_granularity_histograms(monkeypatch):
    """Entity timing histograms should emit one sample per finalized entity."""
    repo = _ConcreteRepo()
    repo._semantic_enabled = True
    repo._embedding_provider = object()
    repo._semantic_embedding_sync_batch_size = 2

    async def _stub_prepare_window(entity_ids: list[int]):
        return [
            _PreparedEntityVectorSync(
                entity_id=entity_id,
                sync_start=0.0,
                source_rows_count=1,
                embedding_jobs=[(100 + entity_id, f"chunk-{entity_id}")],
                prepare_seconds=1.0,
            )
            for entity_id in entity_ids
        ]

    async def _stub_flush(flush_jobs, entity_runtime, synced_entity_ids):
        for job in flush_jobs:
            runtime = entity_runtime[job.entity_id]
            runtime.embed_seconds = 1.0
            runtime.write_seconds = 0.5
            runtime.remaining_jobs = 0
            synced_entity_ids.add(job.entity_id)
        return (2.0, 1.0)

    histogram_calls: list[tuple[str, float, dict]] = []
    counter_calls: list[tuple[str, float, dict]] = []
    perf_counter_values = iter([0.0, 3.0, 4.5, 6.0])

    monkeypatch.setattr(repo, "_prepare_entity_vector_jobs_window", _stub_prepare_window)
    monkeypatch.setattr(repo, "_flush_embedding_jobs", _stub_flush)
    monkeypatch.setattr(
        search_repository_base_module.telemetry,
        "record_histogram",
        lambda name, amount, **attrs: histogram_calls.append((name, amount, attrs)),
    )
    monkeypatch.setattr(
        search_repository_base_module.telemetry,
        "add_counter",
        lambda name, amount, **attrs: counter_calls.append((name, amount, attrs)),
    )
    monkeypatch.setattr(
        search_repository_base_module.time,
        "perf_counter",
        lambda: next(perf_counter_values),
    )

    result = await repo.sync_entity_vectors_batch([1, 2])

    assert result.entities_synced == 2
    histogram_names = [name for name, _, _ in histogram_calls]
    assert histogram_names.count("vector_sync_prepare_seconds") == 2
    assert histogram_names.count("vector_sync_queue_wait_seconds") == 2
    assert histogram_names.count("vector_sync_embed_seconds") == 2
    assert histogram_names.count("vector_sync_write_seconds") == 2
    assert histogram_names.count("vector_sync_batch_total_seconds") == 1
    assert [name for name, _, _ in counter_calls].count("vector_sync_entities_total") == 1


@pytest.mark.asyncio
async def test_sync_entity_vectors_batch_logs_resolved_fastembed_runtime_settings(monkeypatch):
    """Batch start should log the resolved FastEmbed knobs that shape this run."""
    repo = _ConcreteRepo()
    repo._semantic_enabled = True
    repo._embedding_provider = FastEmbedEmbeddingProvider(
        batch_size=128,
        dimensions=384,
        threads=4,
        parallel=2,
    )

    async def _stub_prepare_window(entity_ids: list[int]):
        return [
            _PreparedEntityVectorSync(
                entity_id=entity_id,
                sync_start=0.0,
                source_rows_count=1,
                embedding_jobs=[],
                entity_skipped=True,
            )
            for entity_id in entity_ids
        ]

    info_calls: list[tuple[str, dict]] = []

    def _capture_info(message: str, **kwargs):
        info_calls.append((message, kwargs))

    monkeypatch.setattr(repo, "_prepare_entity_vector_jobs_window", _stub_prepare_window)
    monkeypatch.setattr(search_repository_base_module.logger, "info", _capture_info)

    result = await repo.sync_entity_vectors_batch([1])

    assert result.entities_synced == 1
    runtime_logs = [
        kwargs
        for message, kwargs in info_calls
        if message.startswith("Vector batch runtime settings:")
    ]
    assert len(runtime_logs) == 1
    assert runtime_logs[0]["model_name"] == "bge-small-en-v1.5"
    assert runtime_logs[0]["provider_batch_size"] == 128
    assert runtime_logs[0]["sync_batch_size"] == 64
    assert runtime_logs[0]["threads"] == 4
    assert runtime_logs[0]["configured_parallel"] == 2
    assert runtime_logs[0]["effective_parallel"] == 2
