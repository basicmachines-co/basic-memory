"""SQLite sqlite-vec search repository tests."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
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


def _enable_semantic(
    search_repository: SQLiteSearchRepository,
    embedding_provider: StubEmbeddingProvider | None = None,
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


@pytest.mark.asyncio
async def test_sqlite_prepare_window_uses_shared_reads_and_serialized_write_scope(monkeypatch):
    """SQLite should batch read-side prepare work but serialize write-side mutations."""
    repo = _make_sqlite_repo_for_unit_tests()

    fetched_windows: list[list[int]] = []
    active_write_scopes = 0
    max_active_write_scopes = 0

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
        nonlocal active_write_scopes, max_active_write_scopes
        async with repo._sqlite_prepare_write_lock:
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
        yield AsyncMock()

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
async def test_run_vector_query_caps_k_at_sqlite_vec_limit(search_repository):
    """_run_vector_query must cap the knn k param at SQLITE_VEC_MAX_K (4096).

    sqlite-vec raises OperationalError when k > 4096. The candidate_limit
    passed from the base class can exceed this for large projects, so
    _run_vector_query clamps k while keeping the outer LIMIT unclamped.
    """
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("sqlite-vec k limit is SQLite-specific.")

    _enable_semantic(search_repository)
    await search_repository.init_search_index()

    # Track the parameters passed to session.execute
    captured_params: list[dict] = []

    async def capturing_execute(stmt, params=None):
        if params and "vector_k" in params:
            captured_params.append(dict(params))
        # Return empty result set
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = []
        return mock_result

    async with db.scoped_session(search_repository.session_maker) as session:
        await search_repository._prepare_vector_session(session)
        cast(Any, session).execute = capturing_execute

        query_embedding = [0.1] * search_repository._vector_dimensions

        # candidate_limit exceeds sqlite-vec limit
        await search_repository._run_vector_query(session, query_embedding, 10000)

        assert len(captured_params) == 1
        assert captured_params[0]["vector_k"] == SQLiteSearchRepository.SQLITE_VEC_MAX_K
        assert captured_params[0]["candidate_limit"] == 10000

        # candidate_limit within limit should pass through unchanged
        captured_params.clear()
        await search_repository._run_vector_query(session, query_embedding, 500)

        assert len(captured_params) == 1
        assert captured_params[0]["vector_k"] == 500
        assert captured_params[0]["candidate_limit"] == 500
