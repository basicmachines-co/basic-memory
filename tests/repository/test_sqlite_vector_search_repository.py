"""SQLite sqlite-vec search repository tests."""

import asyncio
import hashlib
from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.litellm_provider import LiteLLMEmbeddingProvider
from basic_memory.repository.prefixing_provider import PrefixingEmbeddingProvider
from basic_memory.repository import search_repository_base as search_repository_base_module
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.semantic_vector_index import (
    VectorIndexScope,
    VectorKey,
    VectorMatch,
    VectorRecord,
)
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.repository import sqlite_vec_index as sqlite_vec_index_module
from basic_memory.repository.sqlite_vec_index import SQLITE_VEC_MAX_K, SQLiteVecIndex
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode


class StubEmbeddingProvider:
    """Deterministic embedding provider for fast repository tests."""

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
    """Same vectors, different model identity to force resync."""

    model_name = "stub-v2"


class RecordingVectorIndex:
    """In-memory adapter with injectable write/delete failures."""

    def __init__(self) -> None:
        self.scope = VectorIndexScope(
            namespace="test",
            project_id=1,
            embedding_identity="test",
            dimensions=4,
        )
        self.records: dict[VectorKey, tuple[float, ...]] = {}
        self.upsert_calls: list[list[VectorRecord]] = []
        self.deleted_entities: list[int] = []
        self.reconcile_calls: list[list[VectorKey]] = []
        self.fail_upsert = False
        self.fail_delete_entity = False
        self.fail_search = False

    async def initialize(self) -> None:
        return None

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        self.upsert_calls.append(list(records))
        if self.fail_upsert:
            raise RuntimeError("adapter write failed")
        self.records.update({record.key: record.values for record in records})

    async def delete(self, keys: Sequence[VectorKey]) -> None:
        for key in keys:
            self.records.pop(key, None)

    async def delete_entity(self, entity_id: int) -> None:
        self.deleted_entities.append(entity_id)
        if self.fail_delete_entity:
            raise RuntimeError("adapter delete failed")
        self.records = {
            key: values for key, values in self.records.items() if key.entity_id != entity_id
        }

    async def delete_orphans(self, live_keys: Sequence[VectorKey]) -> None:
        self.reconcile_calls.append(list(live_keys))
        live_key_set = set(live_keys)
        self.records = {key: values for key, values in self.records.items() if key in live_key_set}

    async def search(
        self,
        query: Sequence[float],
        *,
        limit: int,
    ) -> list[VectorMatch]:
        if self.fail_search:
            raise RuntimeError("adapter query failed")
        return [VectorMatch(key=key, similarity=1.0) for key in list(self.records)[:limit]]


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


def _relation_row(
    *,
    project_id: int,
    row_id: int,
    entity_id: int,
    title: str,
    permalink: str,
    relation_type: str,
) -> SearchIndexRow:
    now = datetime.now(timezone.utc)
    return SearchIndexRow(
        project_id=project_id,
        id=row_id,
        type=SearchItemType.RELATION.value,
        title=title,
        permalink=permalink,
        file_path=f"{permalink}.md",
        metadata=None,
        entity_id=entity_id,
        from_id=entity_id,
        relation_type=relation_type,
        created_at=now,
        updated_at=now,
    )


def _enable_semantic(
    search_repository: SQLiteSearchRepository,
    embedding_provider: EmbeddingProvider | None = None,
) -> None:
    try:
        import sqlite_vec  # noqa: F401
    except ImportError:
        pytest.skip("sqlite-vec dependency is required for sqlite vector repository tests.")

    search_repository._semantic_enabled = True
    provider = embedding_provider or StubEmbeddingProvider()
    search_repository._embedding_provider = provider
    search_repository._vector_dimensions = provider.dimensions
    search_repository._vector_tables_initialized = False


def _make_sqlite_repo_for_unit_tests() -> SQLiteSearchRepository:
    """Build a SQLite repository without touching a real sqlite-vec install."""
    session_maker = MagicMock()
    app_config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/test"},
        default_project="test-project",
        database_backend=DatabaseBackend.SQLITE,
        semantic_search_enabled=True,
        semantic_embedding_sync_batch_size=8,
    )
    repo = SQLiteSearchRepository(
        session_maker,
        project_id=1,
        app_config=app_config,
        embedding_provider=StubEmbeddingProvider(),
    )
    repo._vector_tables_initialized = True
    return repo


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
            "entity_fingerprint",
            "embedding_model",
            "vector_index",
            "embedding_status",
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
async def test_sqlite_vec_reconciliation_is_project_scoped(search_repository):
    """Reconciliation removes non-ready local rows without touching another project."""
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("sqlite-vec reconciliation behavior is local SQLite-only.")

    _enable_semantic(search_repository)
    await search_repository.init_search_index()
    index = cast(SQLiteVecIndex, search_repository._semantic_vector_index)
    embedding_identity = search_repository._embedding_model_key()

    async with db.scoped_session(search_repository.session_maker) as session:
        await index._ensure_loaded(session)
        await session.execute(
            text(
                "INSERT INTO search_vector_chunks ("
                "id, entity_id, project_id, chunk_key, chunk_text, source_hash, "
                "entity_fingerprint, embedding_model, vector_index, embedding_status"
                ") VALUES ("
                ":id, :entity_id, :project_id, :chunk_key, 'text', 'hash', "
                "'fingerprint', :embedding_model, 'sqlite-vec', :embedding_status)"
            ),
            [
                {
                    "id": 901,
                    "entity_id": 901,
                    "project_id": search_repository.project_id,
                    "chunk_key": "entity:901:0",
                    "embedding_model": embedding_identity,
                    "embedding_status": "pending",
                },
                {
                    "id": 902,
                    "entity_id": 902,
                    "project_id": search_repository.project_id,
                    "chunk_key": "entity:902:0",
                    "embedding_model": embedding_identity,
                    "embedding_status": "ready",
                },
                {
                    "id": 903,
                    "entity_id": 903,
                    "project_id": search_repository.project_id + 1,
                    "chunk_key": "entity:903:0",
                    "embedding_model": embedding_identity,
                    "embedding_status": "pending",
                },
            ],
        )
        await session.execute(
            text(
                "INSERT INTO search_vector_embeddings (rowid, embedding) "
                "VALUES (:rowid, :embedding)"
            ),
            [{"rowid": rowid, "embedding": "[1,0,0,0]"} for rowid in (901, 902, 903)],
        )
        await session.commit()

    await index.delete_orphans([])

    async with db.scoped_session(search_repository.session_maker) as session:
        remaining = await session.execute(
            text(
                "SELECT rowid FROM search_vector_embeddings "
                "WHERE rowid IN (901, 902, 903) ORDER BY rowid"
            )
        )
        assert remaining.scalars().all() == [902, 903]


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
async def test_adapter_write_failure_stays_pending_and_retries_idempotently(search_repository):
    """A partial external write must never make an uncommitted vector searchable."""
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("Semantic manifest behavior is exercised through local SQLite.")

    _enable_semantic(search_repository)
    adapter = RecordingVectorIndex()
    adapter.fail_upsert = True
    search_repository._semantic_vector_index = adapter
    search_repository._semantic_vector_index_name = "recording"
    await search_repository.init_search_index()
    await search_repository.index_item(
        _entity_row(
            project_id=search_repository.project_id,
            row_id=111,
            entity_id=111,
            title="Retryable Adapter Write",
            permalink="specs/retryable-adapter-write",
            content_stems="auth token retry",
        )
    )

    with pytest.raises(RuntimeError, match="adapter write failed"):
        await search_repository.sync_entity_vectors(111)

    async with db.scoped_session(search_repository.session_maker) as session:
        failed_state = await session.execute(
            text(
                "SELECT DISTINCT embedding_status FROM search_vector_chunks "
                "WHERE project_id = :project_id AND entity_id = :entity_id"
            ),
            {"project_id": search_repository.project_id, "entity_id": 111},
        )
        assert failed_state.scalars().all() == ["pending"]

    adapter.fail_upsert = False
    await search_repository.sync_entity_vectors(111)

    async with db.scoped_session(search_repository.session_maker) as session:
        recovered_state = await session.execute(
            text(
                "SELECT DISTINCT vector_index, embedding_status FROM search_vector_chunks "
                "WHERE project_id = :project_id AND entity_id = :entity_id"
            ),
            {"project_id": search_repository.project_id, "entity_id": 111},
        )
        assert recovered_state.all() == [("recording", "ready")]

    assert len(adapter.upsert_calls) == 2
    assert [record.key for record in adapter.upsert_calls[0]] == [
        record.key for record in adapter.upsert_calls[1]
    ]


@pytest.mark.asyncio
async def test_ready_commit_failure_retries_same_stable_adapter_key(
    search_repository,
    monkeypatch,
):
    """An adapter success followed by SQL failure must remain a safe idempotent retry."""
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("Semantic manifest behavior is exercised through local SQLite.")

    _enable_semantic(search_repository)
    adapter = RecordingVectorIndex()
    search_repository._semantic_vector_index = adapter
    search_repository._semantic_vector_index_name = "recording"
    await search_repository.init_search_index()

    async with db.scoped_session(search_repository.session_maker) as session:
        inserted = await session.execute(
            text(
                "INSERT INTO search_vector_chunks ("
                "entity_id, project_id, chunk_key, chunk_text, source_hash, "
                "entity_fingerprint, embedding_model, vector_index, embedding_status"
                ") VALUES ("
                ":entity_id, :project_id, :chunk_key, :chunk_text, :source_hash, "
                ":entity_fingerprint, :embedding_model, :vector_index, 'pending'"
                ") RETURNING id"
            ),
            {
                "entity_id": 115,
                "project_id": search_repository.project_id,
                "chunk_key": "entity:115:0",
                "chunk_text": "ready commit retry",
                "source_hash": "hash",
                "entity_fingerprint": "fingerprint",
                "embedding_model": search_repository._embedding_model_key(),
                "vector_index": "recording",
            },
        )
        row_id = int(inserted.scalar_one())
        await session.commit()

    original_scoped_session = search_repository_base_module.db.scoped_session
    context_count = 0

    @asynccontextmanager
    async def fail_ready_commit(session_maker):
        nonlocal context_count
        context_count += 1
        async with original_scoped_session(session_maker) as session:
            if context_count == 2:

                async def fail_commit() -> None:
                    raise RuntimeError("ready commit failed")

                monkeypatch.setattr(session, "commit", fail_commit)
            yield session

    monkeypatch.setattr(
        search_repository_base_module.db,
        "scoped_session",
        fail_ready_commit,
    )
    with pytest.raises(RuntimeError, match="ready commit failed"):
        await search_repository._persist_embeddings(
            [(row_id, "ready commit retry")],
            [[1.0, 0.0, 0.0, 0.0]],
        )
    monkeypatch.setattr(
        search_repository_base_module.db,
        "scoped_session",
        original_scoped_session,
    )

    async with db.scoped_session(search_repository.session_maker) as session:
        failed_status = await session.execute(
            text("SELECT embedding_status FROM search_vector_chunks WHERE id = :row_id"),
            {"row_id": row_id},
        )
        assert failed_status.scalar_one() == "pending"

    await search_repository._persist_embeddings(
        [(row_id, "ready commit retry")],
        [[1.0, 0.0, 0.0, 0.0]],
    )

    async with db.scoped_session(search_repository.session_maker) as session:
        recovered_status = await session.execute(
            text("SELECT embedding_status FROM search_vector_chunks WHERE id = :row_id"),
            {"row_id": row_id},
        )
        assert recovered_status.scalar_one() == "ready"

    assert len(adapter.upsert_calls) == 2
    assert adapter.upsert_calls[0][0].key == adapter.upsert_calls[1][0].key


@pytest.mark.asyncio
async def test_adapter_delete_failure_stays_pending_until_retry(search_repository):
    """External delete failure preserves non-searchable manifest intent for retry."""
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("Semantic manifest behavior is exercised through local SQLite.")

    _enable_semantic(search_repository)
    adapter = RecordingVectorIndex()
    search_repository._semantic_vector_index = adapter
    search_repository._semantic_vector_index_name = "recording"
    await search_repository.init_search_index()
    await search_repository.index_item(
        _entity_row(
            project_id=search_repository.project_id,
            row_id=112,
            entity_id=112,
            title="Retryable Adapter Delete",
            permalink="specs/retryable-adapter-delete",
            content_stems="schema migration retry",
        )
    )
    await search_repository.sync_entity_vectors(112)

    adapter.fail_delete_entity = True
    with pytest.raises(RuntimeError, match="adapter delete failed"):
        await search_repository.delete_entity_vector_rows(112)

    async with db.scoped_session(search_repository.session_maker) as session:
        failed_state = await session.execute(
            text(
                "SELECT DISTINCT embedding_status FROM search_vector_chunks "
                "WHERE project_id = :project_id AND entity_id = :entity_id"
            ),
            {"project_id": search_repository.project_id, "entity_id": 112},
        )
        assert failed_state.scalars().all() == ["pending"]

    adapter.fail_delete_entity = False
    await search_repository.delete_entity_vector_rows(112)

    async with db.scoped_session(search_repository.session_maker) as session:
        row_count = await session.execute(
            text(
                "SELECT COUNT(*) FROM search_vector_chunks "
                "WHERE project_id = :project_id AND entity_id = :entity_id"
            ),
            {"project_id": search_repository.project_id, "entity_id": 112},
        )
        assert row_count.scalar_one() == 0

    assert adapter.deleted_entities == [112, 112]


@pytest.mark.asyncio
async def test_adapter_matches_hydrate_only_current_ready_manifest_rows(search_repository):
    """Stale external matches fail closed unless SQL says the current row is ready."""
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("Semantic manifest behavior is exercised through local SQLite.")

    _enable_semantic(search_repository)
    adapter = RecordingVectorIndex()
    search_repository._semantic_vector_index = adapter
    search_repository._semantic_vector_index_name = "recording"
    await search_repository.init_search_index()
    await search_repository.index_item(
        _entity_row(
            project_id=search_repository.project_id,
            row_id=113,
            entity_id=113,
            title="Manifest Authority",
            permalink="specs/manifest-authority",
            content_stems="queue worker task",
        )
    )
    await search_repository.sync_entity_vectors(113)
    adapter.records[VectorKey(entity_id=999, chunk_key="foreign:999:0")] = (
        1.0,
        0.0,
        0.0,
        0.0,
    )

    ready_results = await search_repository.search(
        search_text="queue worker",
        retrieval_mode=SearchRetrievalMode.VECTOR,
    )
    assert {result.entity_id for result in ready_results} == {113}

    await search_repository.reconcile_vector_index()
    assert set(adapter.records) == set(adapter.reconcile_calls[0])
    assert {key.entity_id for key in adapter.records} == {113}

    adapter.fail_search = True
    with pytest.raises(RuntimeError, match="adapter query failed"):
        await search_repository.search(
            search_text="queue worker",
            retrieval_mode=SearchRetrievalMode.VECTOR,
        )
    adapter.fail_search = False

    async with db.scoped_session(search_repository.session_maker) as session:
        await session.execute(
            text(
                "UPDATE search_vector_chunks SET embedding_status = 'pending' "
                "WHERE project_id = :project_id AND entity_id = :entity_id"
            ),
            {"project_id": search_repository.project_id, "entity_id": 113},
        )
        await session.commit()

    pending_results = await search_repository.search(
        search_text="queue worker",
        retrieval_mode=SearchRetrievalMode.VECTOR,
    )
    assert pending_results == []


@pytest.mark.asyncio
async def test_vector_index_switch_reembeds_manifest_rows(search_repository):
    """Changing the selected adapter identity invalidates otherwise unchanged chunks."""
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("Semantic manifest behavior is exercised through local SQLite.")

    _enable_semantic(search_repository)
    adapter = RecordingVectorIndex()
    search_repository._semantic_vector_index = adapter
    search_repository._semantic_vector_index_name = "recording-a"
    await search_repository.init_search_index()
    await search_repository.index_item(
        _entity_row(
            project_id=search_repository.project_id,
            row_id=114,
            entity_id=114,
            title="Index Switch",
            permalink="specs/index-switch",
            content_stems="database semantic index switch",
        )
    )
    await search_repository.sync_entity_vectors(114)

    search_repository._semantic_vector_index_name = "recording-b"
    await search_repository.sync_entity_vectors(114)

    async with db.scoped_session(search_repository.session_maker) as session:
        state = await session.execute(
            text(
                "SELECT DISTINCT vector_index, embedding_status FROM search_vector_chunks "
                "WHERE project_id = :project_id AND entity_id = :entity_id"
            ),
            {"project_id": search_repository.project_id, "entity_id": 114},
        )
        assert state.all() == [("recording-b", "ready")]

    assert len(adapter.upsert_calls) == 2


@pytest.mark.asyncio
async def test_sqlite_vector_sync_skips_unchanged_and_reembeds_changed_content(search_repository):
    """SQLite vector sync tracks new, changed, unchanged, and model-changed entities."""
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("sqlite-vec repository behavior is local SQLite-only.")

    _enable_semantic(search_repository)
    await search_repository.init_search_index()

    await search_repository.index_item(
        _entity_row(
            project_id=search_repository.project_id,
            row_id=111,
            entity_id=111,
            title="Auth and Schema Notes",
            permalink="specs/auth-and-schema",
            content_stems="# Overview\n- auth token rotation\n- schema migration planning",
        )
    )

    new_result = await search_repository.sync_entity_vectors_batch([111])
    assert new_result.entities_synced == 1
    assert new_result.entities_skipped == 0
    assert new_result.chunks_total >= 2
    assert new_result.chunks_skipped == 0
    assert new_result.embedding_jobs_total == new_result.chunks_total

    async with db.scoped_session(search_repository.session_maker) as session:
        stored_rows = await session.execute(
            text(
                "SELECT entity_fingerprint, embedding_model "
                "FROM search_vector_chunks "
                "WHERE project_id = :project_id AND entity_id = :entity_id"
            ),
            {"project_id": search_repository.project_id, "entity_id": 111},
        )
        metadata_rows = stored_rows.fetchall()
        assert metadata_rows
        assert len({row.entity_fingerprint for row in metadata_rows}) == 1
        assert len({row.embedding_model for row in metadata_rows}) == 1
        assert metadata_rows[0].embedding_model == "StubEmbeddingProvider:stub:4"

    unchanged_result = await search_repository.sync_entity_vectors_batch([111])
    assert unchanged_result.entities_synced == 1
    assert unchanged_result.entities_skipped == 1
    assert unchanged_result.embedding_jobs_total == 0
    assert unchanged_result.queue_wait_seconds_total == pytest.approx(0.0, abs=0.01)
    assert unchanged_result.chunks_skipped == unchanged_result.chunks_total

    await search_repository.index_item(
        _entity_row(
            project_id=search_repository.project_id,
            row_id=111,
            entity_id=111,
            title="Auth and Schema Notes",
            permalink="specs/auth-and-schema",
            content_stems="# Overview\n- auth token rotation\n- database schema migration planning",
        )
    )
    changed_result = await search_repository.sync_entity_vectors_batch([111])
    assert changed_result.entities_synced == 1
    assert changed_result.entities_skipped == 0
    assert changed_result.embedding_jobs_total >= 1
    assert changed_result.chunks_skipped >= 1
    assert changed_result.embedding_jobs_total < changed_result.chunks_total

    _enable_semantic(search_repository, StubEmbeddingProviderV2())
    model_changed_result = await search_repository.sync_entity_vectors_batch([111])
    assert model_changed_result.entities_synced == 1
    assert model_changed_result.entities_skipped == 0
    assert model_changed_result.chunks_skipped == 0
    assert model_changed_result.embedding_jobs_total == model_changed_result.chunks_total

    _enable_semantic(
        search_repository,
        PrefixingEmbeddingProvider(
            StubEmbeddingProviderV2(),
            document_prefix="doc: ",
            query_prefix="query: ",
        ),
    )
    prefix_changed_result = await search_repository.sync_entity_vectors_batch([111])
    assert prefix_changed_result.entities_synced == 1
    assert prefix_changed_result.entities_skipped == 0
    assert prefix_changed_result.chunks_skipped == 0
    assert prefix_changed_result.embedding_jobs_total == prefix_changed_result.chunks_total


def test_sqlite_embedding_model_key_includes_litellm_role_settings():
    """LiteLLM role changes should invalidate previously embedded document chunks."""
    repo = _make_sqlite_repo_for_unit_tests()
    repo._embedding_provider = LiteLLMEmbeddingProvider(
        model_name="nvidia_nim/nvidia/embed-qa-4",
        dimensions=1024,
        document_input_type="passage",
        query_input_type="query",
    )
    passage_query_key = repo._embedding_model_key()

    repo._embedding_provider = LiteLLMEmbeddingProvider(
        model_name="nvidia_nim/nvidia/embed-qa-4",
        dimensions=1024,
        document_input_type="document",
        query_input_type="query",
    )
    document_query_key = repo._embedding_model_key()

    assert passage_query_key != document_query_key
    assert "document_input_type=passage" in passage_query_key
    assert "query_input_type=query" in passage_query_key


def test_sqlite_embedding_model_key_ignores_litellm_api_base():
    """LiteLLM endpoint routing is not part of stored vector identity."""
    repo = _make_sqlite_repo_for_unit_tests()
    repo._embedding_provider = LiteLLMEmbeddingProvider(dimensions=3)
    default_endpoint_key = repo._embedding_model_key()

    repo._embedding_provider = LiteLLMEmbeddingProvider(
        dimensions=3,
        api_base="http://token@example.test/v1",
    )
    custom_endpoint_key = repo._embedding_model_key()

    assert default_endpoint_key == custom_endpoint_key
    assert "api_base" not in custom_endpoint_key
    assert "token@example.test" not in custom_endpoint_key


def test_sqlite_embedding_model_key_includes_literal_prefixes():
    """Literal prefixes change vector semantics and must invalidate stored chunks."""
    repo = _make_sqlite_repo_for_unit_tests()
    repo._embedding_provider = PrefixingEmbeddingProvider(
        StubEmbeddingProvider(),
        document_prefix="title: none | text: ",
        query_prefix="task: search result | query: ",
    )

    key = repo._embedding_model_key()

    assert key.startswith("PrefixingEmbeddingProvider:StubEmbeddingProvider:stub:4:")
    assert f"document_prefix_sha256={hashlib.sha256(b'title: none | text: ').hexdigest()}" in key
    assert (
        f"query_prefix_sha256={hashlib.sha256(b'task: search result | query: ').hexdigest()}" in key
    )
    assert "title: none | text: " not in key
    assert "task: search result | query: " not in key


@pytest.mark.asyncio
async def test_sqlite_prepare_window_uses_shared_reads_and_serialized_write_scope(monkeypatch):
    """SQLite should batch read-side prepare work but serialize write-side mutations."""
    repo = _make_sqlite_repo_for_unit_tests()

    fetched_windows: list[list[int]] = []
    active_write_scopes = 0
    max_active_write_scopes = 0
    write_scope_entries = 0
    sessions: list[AsyncMock] = []

    async def _stub_fetch_source_rows(session, entity_ids: list[int]):
        fetched_windows.append(list(entity_ids))
        return {entity_id: [object()] for entity_id in entity_ids}

    async def _stub_fetch_existing_rows(session, entity_ids: list[int]):
        return {entity_id: [] for entity_id in entity_ids}

    def _stub_build_chunk_records(source_rows):
        return [
            {
                "chunk_key": "entity:1:0",
                "chunk_text": "chunk text",
                "source_hash": "hash",
            }
        ]

    @asynccontextmanager
    async def _track_write_scope():
        nonlocal active_write_scopes, max_active_write_scopes, write_scope_entries
        async with repo._sqlite_prepare_write_lock:
            write_scope_entries += 1
            active_write_scopes += 1
            max_active_write_scopes = max(max_active_write_scopes, active_write_scopes)
            try:
                yield
            finally:
                active_write_scopes -= 1

    async def _stub_upsert(
        session,
        *,
        entity_id: int,
        scheduled_records,
        existing_by_key,
        entity_fingerprint: str,
        embedding_model: str,
    ):
        await asyncio.sleep(0)
        return [(entity_id * 100, scheduled_records[0]["chunk_text"])]

    @asynccontextmanager
    async def fake_scoped_session(session_maker):
        session = AsyncMock()
        sessions.append(session)
        yield session

    monkeypatch.setattr(
        "basic_memory.repository.search_repository_base.db.scoped_session",
        fake_scoped_session,
    )
    monkeypatch.setattr(repo, "_prepare_vector_session", AsyncMock())
    monkeypatch.setattr(repo, "_fetch_prepare_window_source_rows", _stub_fetch_source_rows)
    monkeypatch.setattr(repo, "_fetch_prepare_window_existing_rows", _stub_fetch_existing_rows)
    monkeypatch.setattr(repo, "_build_chunk_records", _stub_build_chunk_records)
    monkeypatch.setattr(repo, "_prepare_entity_write_scope", _track_write_scope)
    monkeypatch.setattr(repo, "_upsert_scheduled_chunk_records", _stub_upsert)

    prepared = await repo._prepare_entity_vector_jobs_window([1, 2])
    prepared_results = [result for result in prepared if not isinstance(result, BaseException)]

    assert fetched_windows == [[1, 2]]
    assert [result.entity_id for result in prepared_results] == [1, 2]
    assert max_active_write_scopes == 1
    assert write_scope_entries == 1
    assert [session.commit.await_count for session in sessions] == [0, 1]


@pytest.mark.asyncio
async def test_sqlite_prepare_window_does_not_deadlock_when_vec_loading_inside_write_scope(
    monkeypatch,
):
    """SQLite should keep vec loading and prepare writes on separate locks."""
    repo = _make_sqlite_repo_for_unit_tests()

    async def _stub_fetch_source_rows(session, entity_ids: list[int]):
        return {entity_id: [object()] for entity_id in entity_ids}

    async def _stub_fetch_existing_rows(session, entity_ids: list[int]):
        return {entity_id: [] for entity_id in entity_ids}

    def _stub_build_chunk_records(source_rows):
        return [
            {
                "chunk_key": "entity:1:0",
                "chunk_text": "chunk text",
                "source_hash": "hash",
            }
        ]

    async def _stub_prepare_vector_session(session):
        # Trigger: SQLite prepare writes call _prepare_vector_session() after
        # entering the write scope.
        # Why: vec loading still needs a lock, but reusing the write lock here
        # would deadlock before the first entity completes.
        # Outcome: this regression test proves the two concerns stay separate.
        async with repo._sqlite_vec_load_lock:
            await asyncio.sleep(0)

    async def _stub_upsert(
        session,
        *,
        entity_id: int,
        scheduled_records,
        existing_by_key,
        entity_fingerprint: str,
        embedding_model: str,
    ):
        return [(entity_id * 100, scheduled_records[0]["chunk_text"])]

    @asynccontextmanager
    async def fake_scoped_session(session_maker):
        yield AsyncMock()

    monkeypatch.setattr(
        "basic_memory.repository.search_repository_base.db.scoped_session",
        fake_scoped_session,
    )
    monkeypatch.setattr(repo, "_fetch_prepare_window_source_rows", _stub_fetch_source_rows)
    monkeypatch.setattr(repo, "_fetch_prepare_window_existing_rows", _stub_fetch_existing_rows)
    monkeypatch.setattr(repo, "_build_chunk_records", _stub_build_chunk_records)
    monkeypatch.setattr(repo, "_prepare_vector_session", _stub_prepare_vector_session)
    monkeypatch.setattr(repo, "_upsert_scheduled_chunk_records", _stub_upsert)

    prepared = await asyncio.wait_for(repo._prepare_entity_vector_jobs_window([1]), timeout=1.0)
    prepared_results = [result for result in prepared if not isinstance(result, BaseException)]

    assert len(prepared_results) == 1
    assert prepared_results[0].entity_id == 1


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
async def test_sqlite_vector_search_survives_cross_type_id_collision(search_repository):
    """Entity and relation rows sharing one numeric id must both hydrate (#982).

    Entity, observation, and relation rows carry ids from independent
    auto-increment sequences, so search_index rows of different types routinely
    share the same numeric id. Keying vector hydration by bare id collapsed
    colliding hits into one dict slot and silently dropped the other result.
    """
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("sqlite-vec repository behavior is local SQLite-only.")

    _enable_semantic(search_repository)
    await search_repository.init_search_index()
    await search_repository.bulk_index_items(
        [
            _entity_row(
                project_id=search_repository.project_id,
                row_id=7,
                entity_id=701,
                title="Auth Token Design",
                permalink="specs/auth-token-design",
                content_stems="auth token session login design",
            ),
            # Same numeric id as the entity row above, different row type.
            _relation_row(
                project_id=search_repository.project_id,
                row_id=7,
                entity_id=702,
                title="login flow relates to auth token design",
                permalink="specs/login-flow/relates-to/auth-token-design",
                relation_type="relates_to",
            ),
        ]
    )
    await search_repository.sync_entity_vectors(701)
    await search_repository.sync_entity_vectors(702)

    results = await search_repository.search(
        search_text="session token auth",
        retrieval_mode=SearchRetrievalMode.VECTOR,
        limit=5,
        offset=0,
    )

    # Both rows match the query; both share id=7 and must survive hydration.
    assert len(results) == 2
    assert {result.type for result in results} == {
        SearchItemType.ENTITY.value,
        SearchItemType.RELATION.value,
    }
    entity_result = next(r for r in results if r.type == SearchItemType.ENTITY.value)
    assert entity_result.permalink == "specs/auth-token-design"

    # The type filter must keep the entity even though a relation shares its id.
    filtered = await search_repository.search(
        search_text="session token auth",
        search_item_types=[SearchItemType.ENTITY],
        retrieval_mode=SearchRetrievalMode.VECTOR,
        limit=5,
        offset=0,
    )
    assert [r.permalink for r in filtered] == ["specs/auth-token-design"]


@pytest.mark.asyncio
async def test_sqlite_hybrid_search_combines_fts_and_vector(search_repository):
    """Hybrid mode fuses FTS and vector results with score-based fusion."""
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


@pytest.mark.asyncio
async def test_run_vector_query_caps_k_at_sqlite_vec_limit(search_repository, monkeypatch):
    """The sqlite-vec adapter caps k while preserving the requested outer limit.

    sqlite-vec raises OperationalError when k > 4096. The candidate_limit
    passed from the base class can exceed this for large projects, so
    _run_vector_query clamps k while keeping the outer LIMIT unclamped.
    """
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("sqlite-vec k limit is SQLite-specific.")

    _enable_semantic(search_repository)
    await search_repository.init_search_index()

    index = cast(SQLiteVecIndex, search_repository._semantic_vector_index)
    captured_params: list[dict] = []
    session = AsyncMock()

    async def capturing_execute(stmt, params=None):
        if params and "vector_k" in params:
            captured_params.append(dict(params))
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = []
        return mock_result

    @asynccontextmanager
    async def fake_scoped_session(_session_maker):
        yield session

    session.execute = capturing_execute
    monkeypatch.setattr(sqlite_vec_index_module.db, "scoped_session", fake_scoped_session)
    monkeypatch.setattr(index, "_ensure_loaded", AsyncMock())
    query_embedding = [0.1] * search_repository._vector_dimensions

    await index.search(query_embedding, limit=10000)

    assert captured_params == [
        {
            "query": "[0.1, 0.1, 0.1, 0.1]",
            "vector_k": SQLITE_VEC_MAX_K,
            "project_id": search_repository.project_id,
            "embedding_identity": search_repository._embedding_model_key(),
            "limit": 10000,
        }
    ]

    captured_params.clear()
    await index.search(query_embedding, limit=500)
    assert captured_params[0]["vector_k"] == 500
    assert captured_params[0]["limit"] == 500
