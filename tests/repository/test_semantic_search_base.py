"""Tests for semantic search orchestration in SearchRepositoryBase."""

import asyncio
import hashlib
from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import override, Any
from unittest.mock import AsyncMock, Mock

import pytest

import basic_memory.repository.search_repository_base as search_repository_base_module
from basic_memory.repository.fastembed_provider import FastEmbedEmbeddingProvider
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_repository_base import (
    SearchRepositoryBase,
    _PreparedEntityVectorSync,
)
from basic_memory.repository.search_trace import SearchTraceCollector
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.repository.semantic_errors import (
    SemanticSearchDisabledError,
    SemanticVectorIndexExtensionError,
)
from basic_memory.repository.semantic_vector_index import (
    VectorDeletion,
    VectorIndexScope,
    VectorKey,
    VectorMatch,
    VectorRecord,
)
from basic_memory.repository.semantic_vector_sync import PendingEmbeddingJob
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode
from basic_memory.temporal import TemporalFilter


# --- Helpers ---


def _pending_job(entity_id: int, row_id: int, chunk_text: str) -> PendingEmbeddingJob:
    return PendingEmbeddingJob(
        entity_id=entity_id,
        chunk_row_id=row_id,
        chunk_key=f"entity:{entity_id}:0",
        chunk_text=chunk_text,
        source_hash=hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
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

    @override
    async def init_search_index(self):
        pass

    @override
    async def get_entity_physical_chunk_keys(self, entity_id: int) -> set[str] | None:
        return None  # physical storage is not inspectable in this double

    @override
    def _prepare_search_term(self, term, is_prefix=True):
        return term

    @override
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
        file_path_prefix: str | None = None,
        temporal: TemporalFilter | None = None,
        retrieval_mode: SearchRetrievalMode = SearchRetrievalMode.FTS,
        min_similarity: float | None = None,
        limit: int = 10,
        offset: int = 0,
        allow_relaxed: bool = False,
        *,
        trace: SearchTraceCollector | None = None,
    ) -> list[SearchIndexRow]:
        return []

    @override
    async def _ensure_vector_tables(self):
        pass

    @override
    async def _run_vector_query(
        self,
        session,
        query_embedding,
        candidate_limit,
        *,
        trace: SearchTraceCollector | None = None,
    ):
        return []

    @override
    async def _write_embeddings(self, session, jobs, embeddings):
        pass

    @override
    async def _delete_entity_chunks(self, session, entity_id, *, expected_deletions=None):
        return []

    @override
    async def _delete_stale_chunks(
        self,
        session,
        stale_ids,
        entity_id,
        *,
        expected_deletions=None,
    ):
        return []

    async def _update_timestamp_sql(self):
        return "CURRENT_TIMESTAMP"

    @override
    def _distance_to_similarity(self, distance: float) -> float:
        return 1.0 / (1.0 + max(distance, 0.0))


class _RecordingVectorIndex:
    """Protocol-complete adapter that records generation-safe upserts."""

    scope = VectorIndexScope(
        namespace="basic-memory-test",
        project_id=1,
        embedding_identity="stub:4",
        dimensions=4,
    )

    def __init__(self) -> None:
        self.upserted_records: list[VectorRecord] = []

    async def initialize(self) -> None:
        return None

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        self.upserted_records.extend(records)

    async def delete(self, records: Sequence[VectorDeletion]) -> None:
        return None

    async def delete_entity(self, entity_id: int) -> None:
        return None

    async def search(
        self,
        query: Sequence[float],
        *,
        limit: int,
    ) -> list[VectorMatch]:
        return []


@pytest.mark.asyncio
async def test_vector_match_hydration_batches_large_adapter_results() -> None:
    """Deep vector pages must not create an unbounded SQL bind-parameter list."""
    repo = _ConcreteRepo()
    repo._semantic_vector_index_name = "milvus"
    repo._embedding_provider = SimpleNamespace(model_name="stub", dimensions=4)
    matches = [
        VectorMatch(
            key=VectorKey(entity_id=entity_id, chunk_key=f"entity:{entity_id}:0"),
            similarity=0.9,
        )
        for entity_id in range(600)
    ]
    session = AsyncMock()

    def hydrated_batch(_statement, params):
        rows = [
            {
                "entity_id": value,
                "chunk_key": params[f"chunk_key_{index}"],
                "chunk_text": f"chunk {value}",
            }
            for index in range((len(params) - 3) // 2)
            if (value := params[f"entity_id_{index}"]) is not None
        ]
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))

    session.execute.side_effect = hydrated_batch

    hydrated = await repo._hydrate_vector_matches(session, matches)

    assert session.execute.await_count == 3
    assert max(len(call.args[1]) for call in session.execute.await_args_list) == 503
    assert [row["entity_id"] for row in hydrated] == list(range(600))


@pytest.mark.asyncio
async def test_external_vector_query_overfetches_past_stale_adapter_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale top-k extension hits must not crowd live manifest rows out."""
    repo = _ConcreteRepo()
    repo._semantic_vector_index_name = "milvus"

    def matches(count: int) -> list[VectorMatch]:
        return [
            VectorMatch(
                key=VectorKey(entity_id=entity_id, chunk_key=f"entity:{entity_id}:0"),
                similarity=0.9,
            )
            for entity_id in range(count)
        ]

    adapter: Any = SimpleNamespace(search=AsyncMock(side_effect=[matches(2), matches(4)]))
    repo._semantic_vector_index = adapter
    live_rows = [
        {"entity_id": 2, "chunk_key": "entity:2:0", "best_similarity": 0.9},
        {"entity_id": 3, "chunk_key": "entity:3:0", "best_similarity": 0.8},
    ]
    hydrate = AsyncMock(side_effect=[[], live_rows])
    monkeypatch.setattr(repo, "_hydrate_vector_matches", hydrate)

    result = await SearchRepositoryBase._run_vector_query(repo, AsyncMock(), [0.1], 2)

    assert result == live_rows
    assert [call.kwargs["limit"] for call in adapter.search.await_args_list] == [2, 4]


@pytest.mark.asyncio
async def test_embedding_persistence_skips_stale_source_generation(monkeypatch) -> None:
    """An obsolete embedding job must not overwrite the current adapter record."""
    repo = _ConcreteRepo()
    repo._semantic_vector_index_name = "pgvector"
    repo._embedding_provider = SimpleNamespace(model_name="stub", dimensions=4)
    adapter = _RecordingVectorIndex()
    repo._semantic_vector_index = adapter
    current_text = "new chunk text"
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(
        mappings=lambda: SimpleNamespace(
            all=lambda: [
                {
                    "id": 7,
                    "entity_id": 41,
                    "chunk_key": "entity:41:0",
                    "source_hash": hashlib.sha256(current_text.encode("utf-8")).hexdigest(),
                }
            ]
        )
    )

    @asynccontextmanager
    async def fake_scoped_session(_session_maker):
        yield session

    monkeypatch.setattr(search_repository_base_module.db, "scoped_session", fake_scoped_session)

    await repo._persist_embeddings(
        [_pending_job(41, 7, "old chunk text")],
        [[1.0, 0.0, 0.0, 0.0]],
    )

    assert adapter.upserted_records == []
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_embedding_ready_update_requires_source_generation(monkeypatch) -> None:
    """Ready state must belong to the exact source text that produced the vector."""
    repo = _ConcreteRepo()
    repo._semantic_vector_index_name = "pgvector"
    repo._embedding_provider = SimpleNamespace(model_name="stub", dimensions=4)
    adapter = _RecordingVectorIndex()
    repo._semantic_vector_index = adapter
    chunk_text = "current chunk text"
    source_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
    session = AsyncMock()
    session.execute.side_effect = [
        SimpleNamespace(
            mappings=lambda: SimpleNamespace(
                all=lambda: [
                    {
                        "id": 7,
                        "entity_id": 41,
                        "chunk_key": "entity:41:0",
                        "source_hash": source_hash,
                    }
                ]
            )
        ),
        SimpleNamespace(),
    ]

    @asynccontextmanager
    async def fake_scoped_session(_session_maker):
        yield session

    monkeypatch.setattr(search_repository_base_module.db, "scoped_session", fake_scoped_session)

    await repo._persist_embeddings(
        [_pending_job(41, 7, chunk_text)],
        [[1.0, 0.0, 0.0, 0.0]],
    )

    assert len(adapter.upserted_records) == 1
    ready_statement, ready_params = session.execute.await_args_list[1].args
    assert "source_hash = :source_hash_0" in str(ready_statement)
    assert ready_params["source_hash_0"] == source_hash


@pytest.mark.asyncio
async def test_external_postgres_upsert_holds_manifest_lock_through_ready_commit(
    monkeypatch,
) -> None:
    """External writes and ready publication share one manifest-lock transaction."""
    repo = _ConcreteRepo()
    repo._semantic_vector_index_name = "milvus"
    repo._embedding_provider = SimpleNamespace(model_name="stub", dimensions=4)
    adapter = _RecordingVectorIndex()
    repo._semantic_vector_index = adapter
    chunk_text = "current chunk text"
    source_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
    session = AsyncMock()
    session.connection.return_value = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    session.execute.side_effect = [
        SimpleNamespace(),
        SimpleNamespace(
            mappings=lambda: SimpleNamespace(
                all=lambda: [
                    {
                        "id": 7,
                        "entity_id": 41,
                        "chunk_key": "entity:41:0",
                        "source_hash": source_hash,
                    }
                ]
            )
        ),
        SimpleNamespace(),
    ]
    context_count = 0

    @asynccontextmanager
    async def fake_scoped_session(_session_maker):
        nonlocal context_count
        context_count += 1
        yield session

    monkeypatch.setattr(search_repository_base_module.db, "scoped_session", fake_scoped_session)

    await repo._persist_embeddings(
        [_pending_job(41, 7, chunk_text)],
        [[1.0, 0.0, 0.0, 0.0]],
    )

    project_lock_statement = session.execute.await_args_list[0].args[0]
    manifest_lock_statement = session.execute.await_args_list[1].args[0]
    ready_statement = session.execute.await_args_list[2].args[0]
    assert str(project_lock_statement).startswith("SELECT id FROM project")
    assert "FOR UPDATE" in str(project_lock_statement)
    assert "FOR UPDATE" in str(manifest_lock_statement)
    assert "embedding_status = 'ready'" in str(ready_statement)
    assert context_count == 1
    assert session.commit.await_count == 1
    assert adapter.upserted_records[0].source_hash == source_hash


@pytest.mark.asyncio
async def test_external_sqlite_upsert_holds_write_lock_through_ready_commit(
    monkeypatch,
) -> None:
    """SQLite external writes use one write-lock transaction through publication."""
    repo = _ConcreteRepo()
    repo._semantic_vector_index_name = "milvus"
    repo._embedding_provider = SimpleNamespace(model_name="stub", dimensions=4)
    adapter = _RecordingVectorIndex()
    repo._semantic_vector_index = adapter
    chunk_text = "current chunk text"
    source_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
    session = AsyncMock()
    session.connection.return_value = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    session.execute.side_effect = [
        SimpleNamespace(),
        SimpleNamespace(
            mappings=lambda: SimpleNamespace(
                all=lambda: [
                    {
                        "id": 7,
                        "entity_id": 41,
                        "chunk_key": "entity:41:0",
                        "source_hash": source_hash,
                    }
                ]
            )
        ),
        SimpleNamespace(),
    ]
    context_count = 0

    @asynccontextmanager
    async def fake_scoped_session(_session_maker):
        nonlocal context_count
        context_count += 1
        yield session

    monkeypatch.setattr(search_repository_base_module.db, "scoped_session", fake_scoped_session)

    await repo._persist_embeddings(
        [_pending_job(41, 7, chunk_text)],
        [[1.0, 0.0, 0.0, 0.0]],
    )

    project_lock_statement = session.execute.await_args_list[0].args[0]
    manifest_lock_statement = session.execute.await_args_list[1].args[0]
    ready_statement = session.execute.await_args_list[2].args[0]
    assert str(project_lock_statement).startswith("UPDATE project SET id = id")
    assert "UPDATE search_vector_chunks SET source_hash = source_hash" in str(
        manifest_lock_statement
    )
    assert "RETURNING id, entity_id, chunk_key, source_hash" in str(manifest_lock_statement)
    assert "embedding_status = 'ready'" in str(ready_statement)
    assert context_count == 1
    assert session.commit.await_count == 1
    assert adapter.upserted_records[0].source_hash == source_hash


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dialect_name", "project_lock_prefix"),
    [
        ("postgresql", "SELECT id FROM project"),
        ("sqlite", "UPDATE project SET id = id"),
    ],
)
async def test_external_reconciliation_holds_project_lock_through_orphan_cleanup(
    monkeypatch,
    dialect_name,
    project_lock_prefix,
) -> None:
    """External reconciliation must serialize its snapshot and orphan deletion."""
    repo = _ConcreteRepo()
    repo._semantic_enabled = True
    repo._semantic_vector_index_name = "milvus"
    repo._embedding_provider = SimpleNamespace(model_name="stub", dimensions=4)
    events: list[str] = []
    adapter: Any = SimpleNamespace(
        scope=_RecordingVectorIndex.scope,
        delete_orphans=AsyncMock(side_effect=lambda _live_keys: events.append("delete_orphans")),
    )
    repo._semantic_vector_index = adapter
    session = AsyncMock()
    session.connection.return_value = SimpleNamespace(dialect=SimpleNamespace(name=dialect_name))

    def execute(statement, _params):
        sql = str(statement)
        if sql.startswith(project_lock_prefix):
            events.append("project_lock")
            return SimpleNamespace()
        if sql.startswith("SELECT entity_id, chunk_key"):
            events.append("manifest_read")
            return SimpleNamespace(
                mappings=lambda: SimpleNamespace(
                    all=lambda: [{"entity_id": 41, "chunk_key": "entity:41:0"}]
                )
            )
        raise AssertionError(f"Unexpected SQL: {sql}")

    session.execute.side_effect = execute
    session.commit.side_effect = lambda: events.append("commit")

    @asynccontextmanager
    async def fake_scoped_session(_session_maker):
        yield session

    monkeypatch.setattr(search_repository_base_module.db, "scoped_session", fake_scoped_session)

    await repo.reconcile_vector_index()

    assert events == ["project_lock", "manifest_read", "delete_orphans", "commit"]
    adapter.delete_orphans.assert_awaited_once_with(
        [VectorKey(entity_id=41, chunk_key="entity:41:0")]
    )


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
@pytest.mark.parametrize("retrieval_mode", [SearchRetrievalMode.VECTOR, SearchRetrievalMode.HYBRID])
async def test_count_rejects_semantic_modes_without_running_search(monkeypatch, retrieval_mode):
    """Semantic counts must not materialize vector or hybrid retrieval."""
    repo = _ConcreteRepo()
    search_calls = []

    async def fail_if_search_runs(**kwargs):
        search_calls.append(kwargs)
        return []

    monkeypatch.setattr(repo, "search", fail_if_search_runs)

    with pytest.raises(ValueError, match="Exact counts are only supported for full-text search"):
        await repo.count(search_text="semantic query", retrieval_mode=retrieval_mode)

    assert search_calls == []


@pytest.mark.asyncio
async def test_project_vector_cleanup_skips_missing_manifest(monkeypatch):
    """A full reindex should remain safe before semantic tables ever existed."""
    repo = _ConcreteRepo()
    session = AsyncMock()
    connection = AsyncMock()
    connection.run_sync.return_value = False
    session.connection.return_value = connection

    @asynccontextmanager
    async def fake_scoped_session(_session_maker):
        yield session

    monkeypatch.setattr(search_repository_base_module.db, "scoped_session", fake_scoped_session)

    await repo.delete_project_vector_rows()

    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_project_vector_cleanup_clears_disabled_manifest(monkeypatch):
    """Disabled semantic search must not preserve stale ready manifest rows."""
    repo = _ConcreteRepo()
    session = AsyncMock()
    connection = AsyncMock()
    connection.run_sync.side_effect = [True, {"embedding_status", "vector_index"}]
    session.connection.return_value = connection
    entity_result = SimpleNamespace(all=lambda: [(41, "pgvector"), (42, "pgvector")])
    session.execute.return_value = entity_result

    @asynccontextmanager
    async def fake_scoped_session(_session_maker):
        yield session

    monkeypatch.setattr(search_repository_base_module.db, "scoped_session", fake_scoped_session)

    await repo.delete_project_vector_rows()

    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert any(statement.startswith("UPDATE search_vector_chunks") for statement in statements)
    assert any(statement.startswith("DELETE FROM search_vector_chunks") for statement in statements)


@pytest.mark.asyncio
async def test_project_vector_cleanup_handles_legacy_manifest_without_status(monkeypatch):
    """Legacy vector manifests should be removed without querying absent status columns."""
    repo = _ConcreteRepo()
    session = AsyncMock()
    connection = AsyncMock()
    connection.run_sync.side_effect = [True, set()]
    connection.dialect.name = "sqlite"
    session.connection.return_value = connection
    session.execute.return_value = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [41])
    )

    @asynccontextmanager
    async def fake_scoped_session(_session_maker):
        yield session

    monkeypatch.setattr(search_repository_base_module.db, "scoped_session", fake_scoped_session)

    await repo.delete_project_vector_rows()

    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert not any("embedding_status" in statement for statement in statements)
    assert any(statement.startswith("DELETE FROM search_vector_chunks") for statement in statements)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dialect_name", "project_lock_prefix"),
    [
        ("postgresql", "SELECT id FROM project"),
        ("sqlite", "UPDATE project SET id = id"),
    ],
)
async def test_project_vector_cleanup_uses_available_adapter(
    monkeypatch,
    dialect_name,
    project_lock_prefix,
):
    """External cleanup should keep its project lock through manifest removal."""
    repo = _ConcreteRepo()
    events: list[str] = []
    adapter: Any = SimpleNamespace(
        initialize=AsyncMock(side_effect=lambda: events.append("initialize")),
        delete_entity=AsyncMock(side_effect=lambda _entity_id: events.append("delete")),
    )
    repo._semantic_vector_index = adapter
    repo._semantic_vector_index_name = "milvus"
    session = AsyncMock()
    connection = AsyncMock()
    connection.dialect.name = dialect_name
    connection.run_sync.side_effect = [True, {"embedding_status", "vector_index"}]
    session.connection.return_value = connection
    marker_session = AsyncMock()
    marker_session.execute.side_effect = lambda *_args, **_kwargs: events.append("durable_stage")
    marker_session.commit.side_effect = lambda: events.append("marker_commit")

    def execute(statement, _params):
        sql = str(statement)
        if sql.startswith(project_lock_prefix):
            events.append("project_lock")
            return SimpleNamespace()
        if sql.startswith("SELECT DISTINCT entity_id, vector_index"):
            events.append("manifest_read")
            return SimpleNamespace(all=lambda: [(41, "milvus"), (42, "milvus")])
        if sql.startswith("UPDATE search_vector_chunks"):
            events.append("stage")
            return SimpleNamespace()
        if sql.startswith("DELETE FROM search_vector_chunks"):
            events.append("manifest_delete")
            return SimpleNamespace()
        raise AssertionError(f"Unexpected SQL: {sql}")

    session.execute.side_effect = execute
    session.commit.side_effect = lambda: events.append("commit")
    scoped_sessions = iter([session, marker_session] if dialect_name == "postgresql" else [session])

    @asynccontextmanager
    async def fake_scoped_session(_session_maker):
        yield next(scoped_sessions)

    monkeypatch.setattr(search_repository_base_module.db, "scoped_session", fake_scoped_session)

    await repo.delete_project_vector_rows()

    adapter.initialize.assert_awaited_once()
    assert adapter.delete_entity.await_args_list == [
        ((41,), {}),
        ((42,), {}),
    ]
    expected_events = [
        "project_lock",
        "manifest_read",
        "initialize",
        "delete",
        "delete",
        "manifest_delete",
        "commit",
    ]
    if dialect_name == "postgresql":
        expected_events[2:2] = ["durable_stage", "marker_commit"]
    else:
        expected_events.insert(2, "stage")
    assert events == expected_events
    assert session.commit.await_count == 1


@pytest.mark.asyncio
async def test_project_vector_cleanup_preserves_manifest_after_adapter_failure(monkeypatch):
    """A reindex must retain external ownership when its adapter is unavailable."""
    repo = _ConcreteRepo()
    adapter: Any = SimpleNamespace(
        initialize=AsyncMock(side_effect=RuntimeError("adapter unavailable")),
        delete_entity=AsyncMock(),
    )
    repo._semantic_vector_index = adapter
    repo._semantic_vector_index_name = "milvus"
    session = AsyncMock()
    connection = AsyncMock()
    connection.dialect.name = "postgresql"
    connection.run_sync.side_effect = [True, {"embedding_status", "vector_index"}]
    session.connection.return_value = connection
    session.execute.side_effect = [
        SimpleNamespace(),
        SimpleNamespace(all=lambda: [(41, "milvus")]),
    ]
    marker_session = AsyncMock()
    scoped_sessions = iter([session, marker_session])

    @asynccontextmanager
    async def fake_scoped_session(_session_maker):
        yield next(scoped_sessions)

    warning = Mock()
    monkeypatch.setattr(search_repository_base_module.db, "scoped_session", fake_scoped_session)
    monkeypatch.setattr(search_repository_base_module.logger, "warning", warning)

    with pytest.raises(RuntimeError, match="adapter unavailable"):
        await repo.delete_project_vector_rows()

    adapter.delete_entity.assert_not_awaited()
    marker_session.commit.assert_awaited_once()
    warning.assert_called_once()
    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert not any(
        statement.startswith("DELETE FROM search_vector_chunks") for statement in statements
    )


@pytest.mark.asyncio
async def test_strict_project_vector_cleanup_preserves_manifest_after_adapter_failure(monkeypatch):
    """Project deletion should retain ownership when its external adapter is unavailable."""
    repo = _ConcreteRepo()
    adapter: Any = SimpleNamespace(
        initialize=AsyncMock(side_effect=RuntimeError("adapter unavailable")),
        delete_entity=AsyncMock(),
    )
    repo._semantic_vector_index = adapter
    repo._semantic_vector_index_name = "milvus"
    session = AsyncMock()
    connection = AsyncMock()
    connection.dialect.name = "postgresql"
    connection.run_sync.side_effect = [True, {"embedding_status", "vector_index"}]
    session.connection.return_value = connection
    session.execute.side_effect = [
        SimpleNamespace(),
        SimpleNamespace(all=lambda: [(41, "milvus")]),
    ]
    marker_session = AsyncMock()
    scoped_sessions = iter([session, marker_session])

    @asynccontextmanager
    async def fake_scoped_session(_session_maker):
        yield next(scoped_sessions)

    monkeypatch.setattr(search_repository_base_module.db, "scoped_session", fake_scoped_session)

    with pytest.raises(RuntimeError, match="adapter unavailable"):
        await repo.delete_project_vector_rows(strict_adapter_cleanup=True)

    marker_session.commit.assert_awaited_once()
    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert not any(
        statement.startswith("DELETE FROM search_vector_chunks") for statement in statements
    )


@pytest.mark.asyncio
async def test_strict_project_vector_cleanup_preserves_manifest_without_adapter(monkeypatch):
    """Disabled semantic search must retain external-vector ownership for retry."""
    repo = _ConcreteRepo()
    session = AsyncMock()
    connection = AsyncMock()
    connection.run_sync.side_effect = [True, {"embedding_status", "vector_index"}]
    session.connection.return_value = connection
    session.execute.return_value = SimpleNamespace(all=lambda: [(41, "milvus")])

    @asynccontextmanager
    async def fake_scoped_session(_session_maker):
        yield session

    monkeypatch.setattr(search_repository_base_module.db, "scoped_session", fake_scoped_session)

    with pytest.raises(SemanticVectorIndexExtensionError, match="owned by external indexes"):
        await repo.delete_project_vector_rows(strict_adapter_cleanup=True)

    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert not any(
        statement.startswith("DELETE FROM search_vector_chunks") for statement in statements
    )


@pytest.mark.asyncio
async def test_project_vector_cleanup_preserves_mismatched_external_owner(monkeypatch):
    """Cleanup must not route old extension rows through the newly configured adapter."""
    repo = _ConcreteRepo()
    adapter: Any = SimpleNamespace(
        initialize=AsyncMock(),
        delete_entity=AsyncMock(),
    )
    repo._semantic_vector_index = adapter
    repo._semantic_vector_index_name = "pgvector"
    session = AsyncMock()
    connection = AsyncMock()
    connection.run_sync.side_effect = [True, {"embedding_status", "vector_index"}]
    session.connection.return_value = connection
    session.execute.return_value = SimpleNamespace(all=lambda: [(41, "milvus")])

    @asynccontextmanager
    async def fake_scoped_session(_session_maker):
        yield session

    monkeypatch.setattr(search_repository_base_module.db, "scoped_session", fake_scoped_session)

    with pytest.raises(SemanticVectorIndexExtensionError, match="milvus"):
        await repo.delete_project_vector_rows()

    adapter.initialize.assert_not_awaited()
    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert not any(statement.startswith("UPDATE search_vector_chunks") for statement in statements)
    assert not any(
        statement.startswith("DELETE FROM search_vector_chunks") for statement in statements
    )


@pytest.mark.asyncio
async def test_entity_vector_cleanup_preserves_mismatched_external_owner() -> None:
    """Entity cleanup must fail before staging rows owned by another extension."""
    repo = _ConcreteRepo()
    adapter: Any = SimpleNamespace()
    repo._semantic_vector_index = adapter
    repo._semantic_vector_index_name = "pgvector"
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(
        mappings=lambda: SimpleNamespace(
            all=lambda: [
                {
                    "id": 7,
                    "chunk_key": "entity:41:0",
                    "source_hash": "hash",
                    "vector_index": "milvus",
                }
            ]
        )
    )

    with pytest.raises(SemanticVectorIndexExtensionError, match="milvus"):
        await SearchRepositoryBase._delete_entity_chunks(repo, session, 41)

    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_strict_project_vector_cleanup_allows_disabled_builtin_owner(monkeypatch):
    """Project deletion should not require the semantic stack for built-in vectors."""
    repo = _ConcreteRepo()
    repo._semantic_vector_index_name = "pgvector"
    session = AsyncMock()
    connection = AsyncMock()
    connection.run_sync.side_effect = [True, {"embedding_status", "vector_index"}]
    session.connection.return_value = connection
    session.execute.return_value = SimpleNamespace(all=lambda: [(41, "pgvector")])

    @asynccontextmanager
    async def fake_scoped_session(_session_maker):
        yield session

    monkeypatch.setattr(search_repository_base_module.db, "scoped_session", fake_scoped_session)

    await repo.delete_project_vector_rows(strict_adapter_cleanup=True)

    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert any(statement.startswith("DELETE FROM search_vector_chunks") for statement in statements)


@pytest.mark.asyncio
async def test_external_entity_cleanup_uses_matching_project_adapter(monkeypatch) -> None:
    """DB-first deletes should durably stage manifests before invoking the extension."""
    repo = _ConcreteRepo()
    events: list[str] = []
    adapter: Any = SimpleNamespace(
        initialize=AsyncMock(side_effect=lambda: events.append("initialize")),
        delete_entity=AsyncMock(side_effect=lambda _entity_id: events.append("delete")),
    )
    repo._semantic_vector_index = adapter
    repo._semantic_vector_index_name = "milvus"
    caller_session = AsyncMock()
    caller_session.connection.return_value = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql")
    )
    marker_session = AsyncMock()

    def caller_execute(statement, _params):
        sql = str(statement)
        if sql.startswith("SELECT id FROM project"):
            events.append("project_lock")
            return SimpleNamespace()
        if sql.startswith("SELECT DISTINCT vector_index"):
            events.append("ownership_read")
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: ["milvus"]))
        raise AssertionError(f"Unexpected caller SQL: {sql}")

    caller_session.execute.side_effect = caller_execute
    marker_session.execute.side_effect = lambda *_args, **_kwargs: events.append("stage")
    marker_session.commit.side_effect = lambda: events.append("marker_commit")

    @asynccontextmanager
    async def fake_scoped_session(_session_maker):
        yield marker_session

    monkeypatch.setattr(search_repository_base_module.db, "scoped_session", fake_scoped_session)

    await repo.delete_external_entity_vectors(
        caller_session,
        [41, 42],
    )

    adapter.initialize.assert_awaited_once()
    assert adapter.delete_entity.await_args_list == [((41,), {}), ((42,), {})]
    assert events == [
        "project_lock",
        "ownership_read",
        "stage",
        "marker_commit",
        "initialize",
        "delete",
        "delete",
    ]
    stage_statement = str(marker_session.execute.await_args.args[0])
    assert stage_statement.startswith(
        "UPDATE search_vector_chunks SET embedding_status = 'pending'"
    )


@pytest.mark.asyncio
async def test_external_entity_cleanup_rejects_mismatched_adapter() -> None:
    """A configured adapter must not delete rows owned by another extension."""
    repo = _ConcreteRepo()
    repo._semantic_vector_index_name = "milvus"
    adapter: Any = SimpleNamespace()
    repo._semantic_vector_index = adapter
    session = AsyncMock()
    session.connection.return_value = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    session.execute.side_effect = [
        SimpleNamespace(),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: ["pinecone"])),
    ]

    with pytest.raises(SemanticVectorIndexExtensionError, match="owned by"):
        await repo.delete_external_entity_vectors(
            session,
            [41],
        )


@pytest.mark.asyncio
async def test_sync_entity_vectors_batch_flushes_at_configured_threshold(monkeypatch):
    """Batch sync should flush queued jobs at semantic_embedding_sync_batch_size boundaries."""
    repo = _ConcreteRepo()
    repo._semantic_enabled = True
    repo._embedding_provider = SimpleNamespace(model_name="stub", dimensions=4)
    repo._semantic_embedding_sync_batch_size = 2

    prepared_by_entity = {
        1: _PreparedEntityVectorSync(1, 1.0, 1, [_pending_job(1, 101, "chunk-1")]),
        2: _PreparedEntityVectorSync(2, 2.0, 1, [_pending_job(2, 102, "chunk-2")]),
        3: _PreparedEntityVectorSync(3, 3.0, 1, [_pending_job(3, 103, "chunk-3")]),
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
    assert result.failed_entity_ids == ()
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
    repo._embedding_provider = SimpleNamespace(model_name="stub", dimensions=4)

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
    repo._embedding_provider = SimpleNamespace(model_name="stub", dimensions=4)
    repo._semantic_embedding_sync_batch_size = 2

    prepared_by_entity = {
        1: _PreparedEntityVectorSync(1, 1.0, 1, []),
        2: _PreparedEntityVectorSync(2, 2.0, 1, [_pending_job(2, 102, "chunk-2")]),
        3: _PreparedEntityVectorSync(3, 3.0, 1, [_pending_job(3, 103, "chunk-3")]),
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
    repo._embedding_provider = SimpleNamespace(model_name="stub", dimensions=4)
    repo._semantic_embedding_sync_batch_size = 1

    async def _stub_prepare_window(entity_ids: list[int]):
        prepared = []
        for entity_id in entity_ids:
            if entity_id == 2:
                prepared.append(RuntimeError("prepare failed"))
                continue
            prepared.append(
                _PreparedEntityVectorSync(
                    entity_id,
                    float(entity_id),
                    1,
                    [_pending_job(entity_id, 100 + entity_id, "chunk")],
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
    assert result.failed_entity_ids == (2, 3)


@pytest.mark.asyncio
async def test_sync_entity_vectors_batch_only_attributes_queue_wait_to_flushed_entities(
    monkeypatch,
):
    """Mixed batches should only charge queue wait to entities that entered flush work."""
    repo = _ConcreteRepo()
    repo._semantic_enabled = True
    repo._embedding_provider = SimpleNamespace(model_name="stub", dimensions=4)
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
                    embedding_jobs=[_pending_job(2, 102, "chunk-2")],
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
    repo._embedding_provider = SimpleNamespace(model_name="stub", dimensions=4)
    repo._semantic_embedding_sync_batch_size = 2

    async def _stub_prepare_window(entity_ids: list[int]):
        return [
            _PreparedEntityVectorSync(
                entity_id=entity_id,
                sync_start=0.0,
                source_rows_count=1,
                embedding_jobs=[_pending_job(entity_id, 100 + entity_id, f"chunk-{entity_id}")],
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

    logged_completion: list[dict[str, Any]] = []

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
    monkeypatch.setattr(repo, "_delete_entity_chunks", AsyncMock(return_value=[]))
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
    repo._embedding_provider = SimpleNamespace(model_name="stub", dimensions=4)
    repo._semantic_embedding_sync_batch_size = 2

    async def _stub_prepare_window(entity_ids: list[int]):
        return [
            _PreparedEntityVectorSync(
                entity_id=entity_id,
                sync_start=0.0,
                source_rows_count=1,
                embedding_jobs=[_pending_job(entity_id, 100 + entity_id, f"chunk-{entity_id}")],
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

    histogram_calls: list[tuple[str, float, dict[str, Any]]] = []
    counter_calls: list[tuple[str, float, dict[str, Any]]] = []
    perf_counter_values = iter([0.0, 3.0, 4.5, 6.0])

    class _FakeHistogram:
        def __init__(self, name: str) -> None:
            self.name = name

        def record(self, amount, attributes=None) -> None:
            histogram_calls.append((self.name, amount, attributes or {}))

    class _FakeCounter:
        def __init__(self, name: str) -> None:
            self.name = name

        def add(self, amount, attributes=None) -> None:
            counter_calls.append((self.name, amount, attributes or {}))

    monkeypatch.setattr(repo, "_prepare_entity_vector_jobs_window", _stub_prepare_window)
    monkeypatch.setattr(repo, "_flush_embedding_jobs", _stub_flush)
    monkeypatch.setattr(
        search_repository_base_module.logfire,
        "metric_histogram",
        lambda name, **kwargs: _FakeHistogram(name),
    )
    monkeypatch.setattr(
        search_repository_base_module.logfire,
        "metric_counter",
        lambda name, **kwargs: _FakeCounter(name),
    )
    monkeypatch.setattr(
        search_repository_base_module.time,
        "perf_counter",
        lambda: next(perf_counter_values),
    )

    result = await repo.sync_entity_vectors_batch([1, 2])

    # Batch-level histograms record once per batch using aggregated totals
    # from VectorSyncBatchResult — not per entity. See _sync_entity_vectors_internal.
    assert result.entities_synced == 2
    histogram_names = [name for name, _, _ in histogram_calls]
    assert histogram_names.count("vector_sync_prepare_seconds") == 1
    assert histogram_names.count("vector_sync_queue_wait_seconds") == 1
    assert histogram_names.count("vector_sync_embed_seconds") == 1
    assert histogram_names.count("vector_sync_write_seconds") == 1
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

    info_calls: list[tuple[str, dict[str, Any]]] = []

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
