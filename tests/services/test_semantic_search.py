"""Semantic search service regression tests for local SQLite search."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.config import DatabaseBackend
from basic_memory.repository import EntityRepository
from basic_memory.runtime.vector_sync import VectorSyncBatchResult
from basic_memory.repository.semantic_errors import (
    SemanticDependenciesMissingError,
    SemanticSearchDisabledError,
)
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.schemas.search import SearchItemType, SearchQuery, SearchRetrievalMode


def _sqlite_repo(search_service) -> SQLiteSearchRepository:
    repository = search_service.repository
    if not isinstance(repository, SQLiteSearchRepository):
        pytest.skip("Semantic retrieval behavior is local SQLite-only in this phase.")
    return repository


@pytest.mark.asyncio
async def test_semantic_vector_search_fails_when_disabled(search_service, test_graph):
    """Vector mode should fail fast when semantic search is disabled."""
    repository = _sqlite_repo(search_service)
    repository._semantic_enabled = False

    with pytest.raises(SemanticSearchDisabledError):
        await search_service.search(
            SearchQuery(
                text="Connected Entity",
                retrieval_mode=SearchRetrievalMode.VECTOR,
            )
        )


@pytest.mark.asyncio
async def test_semantic_hybrid_search_fails_when_disabled(search_service, test_graph):
    """Hybrid mode should fail fast when semantic search is disabled."""
    repository = _sqlite_repo(search_service)
    repository._semantic_enabled = False

    with pytest.raises(SemanticSearchDisabledError):
        await search_service.search(
            SearchQuery(
                text="Root Entity",
                retrieval_mode=SearchRetrievalMode.HYBRID,
            )
        )


@pytest.mark.asyncio
async def test_semantic_vector_search_fails_when_provider_unavailable(search_service, test_graph):
    """Vector mode should fail fast when semantic provider is unavailable."""
    repository = _sqlite_repo(search_service)
    repository._semantic_enabled = True
    repository._embedding_provider = None
    repository._vector_tables_initialized = False

    with pytest.raises(SemanticDependenciesMissingError):
        await search_service.search(
            SearchQuery(
                text="Root Entity",
                retrieval_mode=SearchRetrievalMode.VECTOR,
            )
        )


@pytest.mark.asyncio
async def test_semantic_vector_mode_rejects_non_text_query(search_service, test_graph):
    """Vector mode should not silently fall back for title-only queries."""
    with pytest.raises(ValueError):
        await search_service.search(
            SearchQuery(
                title="Root",
                retrieval_mode=SearchRetrievalMode.VECTOR,
                entity_types=[SearchItemType.ENTITY],
            )
        )


@pytest.mark.asyncio
async def test_semantic_fts_mode_still_returns_observations(search_service, test_graph):
    """Explicit FTS mode should preserve existing mixed result behavior."""
    results = await search_service.search(
        SearchQuery(
            text="Root note 1",
            retrieval_mode=SearchRetrievalMode.FTS,
        )
    )

    assert results
    assert any(result.type == SearchItemType.OBSERVATION.value for result in results)


@pytest.mark.asyncio
async def test_semantic_vector_sync_skips_embed_opt_out_and_clears_vectors(
    search_service, monkeypatch
):
    """Embed opt-out should clear stale vectors instead of regenerating them."""
    repository = _sqlite_repo(search_service)
    repository._semantic_enabled = True

    monkeypatch.setattr(
        search_service.entity_repository,
        "find_by_id",
        AsyncMock(return_value=SimpleNamespace(id=42, entity_metadata={"embed": False})),
    )
    sync_vectors = AsyncMock()
    delete_entity_vectors = AsyncMock()
    monkeypatch.setattr(repository, "sync_entity_vectors", sync_vectors)
    monkeypatch.setattr(repository, "delete_entity_vector_rows", delete_entity_vectors)

    await search_service.sync_entity_vectors(42)

    sync_vectors.assert_not_awaited()
    delete_entity_vectors.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_semantic_vector_sync_resumes_when_embed_opt_out_removed(search_service, monkeypatch):
    """Removing the opt-out should restore normal embedding sync."""
    repository = _sqlite_repo(search_service)
    repository._semantic_enabled = True

    monkeypatch.setattr(
        search_service.entity_repository,
        "find_by_id",
        AsyncMock(return_value=SimpleNamespace(id=42, entity_metadata={})),
    )
    sync_vectors = AsyncMock()
    execute_query = AsyncMock()
    monkeypatch.setattr(repository, "sync_entity_vectors", sync_vectors)
    monkeypatch.setattr(repository, "execute_query", execute_query)

    await search_service.sync_entity_vectors(42)

    sync_vectors.assert_awaited_once_with(42)
    execute_query.assert_not_awaited()


@pytest.mark.asyncio
async def test_semantic_vector_sync_batch_skips_embed_opt_out_and_reports_skips(
    search_service, monkeypatch
):
    """Batch vector sync should only embed eligible notes and report skipped opt-outs."""
    repository = _sqlite_repo(search_service)
    repository._semantic_enabled = True

    monkeypatch.setattr(
        search_service.entity_repository,
        "find_by_ids",
        AsyncMock(
            return_value=[
                SimpleNamespace(id=41, entity_metadata={"embed": False}),
                SimpleNamespace(id=42, entity_metadata={}),
            ]
        ),
    )
    sync_batch = AsyncMock(
        return_value=VectorSyncBatchResult(
            entities_total=1,
            entities_synced=1,
            entities_failed=0,
        )
    )
    delete_entity_vectors = AsyncMock()
    monkeypatch.setattr(repository, "sync_entity_vectors_batch", sync_batch)
    monkeypatch.setattr(repository, "delete_entity_vector_rows", delete_entity_vectors)

    result = await search_service.sync_entity_vectors_batch([41, 42])

    sync_batch.assert_awaited_once()
    sync_batch_args = sync_batch.await_args
    assert sync_batch_args is not None
    assert sync_batch_args.args[0] == [42]
    assert result.entities_total == 2
    assert result.entities_synced == 1
    assert result.entities_skipped == 1
    delete_entity_vectors.assert_awaited_once_with(41)


@pytest.mark.asyncio
async def test_semantic_vector_sync_empty_batch_preserves_index_identity(
    search_service,
    monkeypatch,
):
    repository = _sqlite_repo(search_service)
    expected = VectorSyncBatchResult(
        entities_total=0,
        entities_synced=0,
        entities_failed=0,
        vector_index="sqlite-vec",
        embedding_model="FastEmbedEmbeddingProvider:test-model",
    )
    sync_batch = AsyncMock(return_value=expected)
    monkeypatch.setattr(repository, "sync_entity_vectors_batch", sync_batch)

    result = await search_service.sync_entity_vectors_batch([])

    assert result is expected
    sync_batch.assert_awaited_once_with([])


@pytest.mark.asyncio
async def test_embed_opt_out_note_still_participates_in_fts(
    search_service, session_maker, test_project
):
    """Per-note semantic opt-out should not remove the note from FTS search."""
    entity_repo = EntityRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        entity = await entity_repo.create(
            session,
            {
                "title": "FTS Opt Out",
                "note_type": "note",
                "entity_metadata": {"embed": False},
                "content_type": "text/markdown",
                "file_path": "test/fts-opt-out.md",
                "permalink": "test/fts-opt-out",
                "project_id": test_project.id,
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
            },
        )

    await search_service.index_entity(
        entity,
        content="This note should stay searchable through full text indexing.",
    )

    results = await search_service.search(
        SearchQuery(
            text="stay searchable",
            retrieval_mode=SearchRetrievalMode.FTS,
        )
    )

    assert any(result.entity_id == entity.id for result in results)


@pytest.mark.asyncio
async def test_reindex_vectors_respects_embed_opt_out(search_service, monkeypatch):
    """Full vector reindex should route through the service-level opt-out filter."""
    monkeypatch.setattr(
        search_service.entity_repository,
        "find_all",
        AsyncMock(
            return_value=[
                SimpleNamespace(id=41, entity_metadata={"embed": False}),
                SimpleNamespace(id=42, entity_metadata={}),
            ]
        ),
    )
    purge_stale_rows = AsyncMock()
    sync_batch = AsyncMock(
        return_value=VectorSyncBatchResult(
            entities_total=2,
            entities_synced=0,
            entities_failed=1,
            entities_skipped=1,
            sample_errors=("embedding service unavailable",),
            vector_index="sqlite-vec",
            embedding_model="FastEmbedEmbeddingProvider:test-model",
        )
    )
    monkeypatch.setattr(search_service, "_purge_stale_search_rows", purge_stale_rows)
    monkeypatch.setattr(search_service, "sync_entity_vectors_batch", sync_batch)
    reconcile = AsyncMock()
    monkeypatch.setattr(search_service.repository, "reconcile_vector_index", reconcile)

    stats = await search_service.reindex_vectors()

    purge_stale_rows.assert_awaited_once()
    sync_batch.assert_awaited_once_with([41, 42], progress_callback=None)
    reconcile.assert_awaited_once()
    assert stats == {
        "total_entities": 2,
        "embedded": 0,
        "skipped": 1,
        "errors": 1,
        "sample_errors": ("embedding service unavailable",),
        "vector_index": "sqlite-vec",
        "embedding_model": "FastEmbedEmbeddingProvider:test-model",
    }


@pytest.mark.asyncio
async def test_reindex_vectors_purges_sqlite_vectors_before_sync(search_service, monkeypatch):
    """Regression for #829: stale vec0 rows must be purged before batch sync."""
    repository = _sqlite_repo(search_service)
    repository._semantic_enabled = True
    calls: list[str] = []

    monkeypatch.setattr(
        search_service.entity_repository,
        "find_all",
        AsyncMock(return_value=[SimpleNamespace(id=42, entity_metadata={})]),
    )

    async def delete_stale_vector_rows():
        calls.append("purge")

    async def sync_entity_vectors_batch(entity_ids, progress_callback=None):
        assert entity_ids == [42]
        assert progress_callback is None
        calls.append("sync")
        return VectorSyncBatchResult(
            entities_total=1,
            entities_synced=1,
            entities_failed=0,
            vector_index="sqlite-vec",
            embedding_model="FastEmbedEmbeddingProvider:test-model",
        )

    monkeypatch.setattr(repository, "delete_stale_vector_rows", delete_stale_vector_rows)
    monkeypatch.setattr(search_service, "sync_entity_vectors_batch", sync_entity_vectors_batch)
    reconcile = AsyncMock()
    monkeypatch.setattr(repository, "reconcile_vector_index", reconcile)

    stats = await search_service.reindex_vectors()

    assert calls == ["purge", "sync"]
    reconcile.assert_awaited_once()
    assert stats == {
        "total_entities": 1,
        "embedded": 1,
        "skipped": 0,
        "errors": 0,
        "sample_errors": (),
        "vector_index": "sqlite-vec",
        "embedding_model": "FastEmbedEmbeddingProvider:test-model",
    }


@pytest.mark.asyncio
async def test_reindex_all_uses_vector_adapter_cleanup(search_service, monkeypatch):
    """Full service reindex should clean vectors through the repository boundary."""
    repository = _sqlite_repo(search_service)
    executed_sql: list[str] = []
    calls: list[str] = []

    async def execute_query(query, params=None):
        executed_sql.append(str(query))

    async def delete_project_vector_rows():
        calls.append("delete_project_vector_rows")

    monkeypatch.setattr(repository, "execute_query", execute_query)
    monkeypatch.setattr(repository, "delete_project_vector_rows", delete_project_vector_rows)
    monkeypatch.setattr(search_service, "init_search_index", AsyncMock())
    monkeypatch.setattr(search_service.entity_repository, "find_all", AsyncMock(return_value=[]))

    await search_service.reindex_all()

    assert calls == ["delete_project_vector_rows"]
    assert all("search_vector_embeddings" not in sql for sql in executed_sql)


@pytest.mark.asyncio
async def test_drop_vector_tables_skips_sqlite_vec_load_for_plain_table(
    search_service,
    monkeypatch,
):
    """Plain compatibility tables should not require sqlite-vec to be loaded."""
    repository = _sqlite_repo(search_service)
    await repository.drop_vector_tables()

    async with db.scoped_session(repository.session_maker) as session:
        await session.execute(
            text("CREATE TABLE search_vector_embeddings (rowid INTEGER PRIMARY KEY)")
        )
        await session.commit()

    async def fail_if_loaded(_session):
        raise AssertionError("plain tables should not load sqlite-vec")

    monkeypatch.setattr(repository, "_ensure_sqlite_vec_loaded", fail_if_loaded)

    await repository.drop_vector_tables()

    async with db.scoped_session(repository.session_maker) as session:
        result = await session.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'search_vector_embeddings'"
            )
        )
        assert result.scalar() is None


@pytest.mark.asyncio
async def test_reindex_vectors_force_full_clears_project_vectors_before_resync(
    search_service, monkeypatch
):
    """Force-full vector reindex should clear derived vectors before batch sync."""
    repository = _sqlite_repo(search_service)
    repository._semantic_enabled = True

    monkeypatch.setattr(
        search_service.entity_repository,
        "find_all",
        AsyncMock(
            return_value=[
                SimpleNamespace(id=41, entity_metadata={}),
                SimpleNamespace(id=42, entity_metadata={}),
            ]
        ),
    )
    purge_stale_rows = AsyncMock()
    delete_project_vectors = AsyncMock()
    sync_batch = AsyncMock(
        return_value=VectorSyncBatchResult(
            entities_total=2,
            entities_synced=2,
            entities_failed=0,
            vector_index="sqlite-vec",
            embedding_model="FastEmbedEmbeddingProvider:test-model",
        )
    )
    monkeypatch.setattr(search_service, "_purge_stale_search_rows", purge_stale_rows)
    monkeypatch.setattr(repository, "delete_project_vector_rows", delete_project_vectors)
    reconcile = AsyncMock()
    monkeypatch.setattr(repository, "reconcile_vector_index", reconcile)
    monkeypatch.setattr(search_service, "sync_entity_vectors_batch", sync_batch)

    stats = await search_service.reindex_vectors(force_full=True)

    purge_stale_rows.assert_awaited_once()
    delete_project_vectors.assert_awaited_once()
    sync_batch.assert_awaited_once_with([41, 42], progress_callback=None)
    reconcile.assert_awaited_once()
    assert stats == {
        "total_entities": 2,
        "embedded": 2,
        "skipped": 0,
        "errors": 0,
        "sample_errors": (),
        "vector_index": "sqlite-vec",
        "embedding_model": "FastEmbedEmbeddingProvider:test-model",
    }


@pytest.mark.asyncio
async def test_semantic_vector_sync_batch_cleans_up_unknown_ids(search_service, monkeypatch):
    """Deleted entity IDs should still flow through repository cleanup instead of being dropped."""
    repository = _sqlite_repo(search_service)
    repository._semantic_enabled = True

    monkeypatch.setattr(
        search_service.entity_repository,
        "find_by_ids",
        AsyncMock(return_value=[SimpleNamespace(id=42, entity_metadata={})]),
    )
    sync_batch = AsyncMock(
        side_effect=[
            VectorSyncBatchResult(
                entities_total=1,
                entities_synced=1,
                entities_failed=0,
                entities_skipped=1,
                sample_errors=("shared failure", "cleanup failure"),
                vector_index="sqlite-vec",
                embedding_model="FastEmbedEmbeddingProvider:test-model",
            ),
            VectorSyncBatchResult(
                entities_total=1,
                entities_synced=1,
                entities_failed=0,
                sample_errors=("shared failure", "embedding failure", "extra failure"),
                vector_index="sqlite-vec",
                embedding_model="FastEmbedEmbeddingProvider:test-model",
            ),
        ]
    )
    monkeypatch.setattr(repository, "sync_entity_vectors_batch", sync_batch)
    progress_callback = AsyncMock()

    result = await search_service.sync_entity_vectors_batch([41, 42], progress_callback)

    assert sync_batch.await_count == 2
    called_entity_ids = {tuple(call.args[0]) for call in sync_batch.await_args_list}
    assert called_entity_ids == {(41,), (42,)}
    progress_callback_calls = [
        call
        for call in sync_batch.await_args_list
        if call.kwargs.get("progress_callback") is not None
    ]
    assert len(progress_callback_calls) == 1
    assert progress_callback_calls[0].args[0] == [42]
    assert progress_callback_calls[0].kwargs["progress_callback"] is progress_callback
    assert result.entities_total == 2
    assert result.entities_synced == 2
    assert result.entities_failed == 0
    assert result.entities_skipped == 0
    assert result.sample_errors == (
        "shared failure",
        "cleanup failure",
        "embedding failure",
    )
    assert result.vector_index == "sqlite-vec"
    assert result.embedding_model == "FastEmbedEmbeddingProvider:test-model"


class _RecordingEmbeddingProvider:
    """Deterministic embedder that records every text it is asked to embed."""

    model_name = "recording"
    dimensions = 4

    def __init__(self) -> None:
        self.embedded_texts: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0, 1.0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.embedded_texts.extend(texts)
        return [[0.0, 0.0, 0.0, 1.0] for _ in texts]

    def runtime_log_attrs(self) -> dict[str, object]:
        return {}


@pytest.mark.asyncio
async def test_note_update_does_not_reembed_unchanged_observations_or_relations(
    search_service, entity_service, app_config, session_maker, test_project
):
    """Editing one line of a note re-embeds only that line's chunk.

    Updating a note deletes and recreates its observations and relations, so their
    search rows get new ids. Unchanged text must keep its embedding anyway: an agent
    appending to a large ledger note otherwise pays to re-embed every observation and
    relation on every edit (production tenant 0fffb994, about 86 chunks per edit).
    Runs on both backends; Postgres is what Cloud uses and never reuses row ids.
    """
    from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
    from basic_memory.schemas import Entity as EntitySchema
    from basic_memory.services.search_service import SearchService

    provider = _RecordingEmbeddingProvider()
    semantic_config = app_config
    if app_config.database_backend == DatabaseBackend.POSTGRES:
        async with db.scoped_session(session_maker) as session:
            try:
                await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await session.commit()
            except Exception:
                pytest.skip("pgvector extension is unavailable in this Postgres test environment.")
        repository = PostgresSearchRepository(
            session_maker,
            project_id=test_project.id,
            app_config=semantic_config,
            embedding_provider=provider,
        )
    else:
        try:
            import sqlite_vec  # noqa: F401
        except ImportError:
            pytest.skip("sqlite-vec dependency is required for vector sync tests.")
        repository = SQLiteSearchRepository(
            session_maker,
            project_id=test_project.id,
            app_config=semantic_config,
            embedding_provider=provider,
        )
    semantic_service = SearchService(
        repository,
        search_service.entity_repository,
        search_service.file_service,
        session_maker=session_maker,
    )
    await semantic_service.init_search_index()

    def note_content(status_line: str) -> str:
        return (
            f"{status_line}\n\n"
            "## Observations\n"
            "- [decision] Ledger notes stay in plain Markdown\n"
            "- [finding] Agents append to ledgers many times a day\n"
            "- [risk] Large notes amplify indexing cost\n\n"
            "## Relations\n"
            "- relates_to [[Indexing Pipeline]]\n"
            "- depends_on [[Embedding Provider]]\n"
        )

    entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Ledger",
            directory="ledgers",
            note_type="note",
            content=note_content("Status: drafting the first entry."),
        )
    )
    await semantic_service.index_entity(entity)
    first_sync = await repository.sync_entity_vectors_batch([entity.id])
    assert first_sync.embedding_jobs_total > 0
    assert any("Large notes amplify indexing cost" in text for text in provider.embedded_texts)
    assert any("Indexing Pipeline" in text for text in provider.embedded_texts)

    # Another note written in between takes the next observation ids, as in any real
    # workspace. Without it SQLite reuses the deleted ids on rewrite and hides the bug;
    # Postgres sequences never reuse ids.
    other, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Other Note",
            directory="ledgers",
            note_type="note",
            content="## Observations\n- [fact] Written between ledger edits\n",
        )
    )
    await semantic_service.index_entity(other)

    # Change only the status line; every observation and relation is unchanged.
    provider.embedded_texts.clear()
    entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Ledger",
            directory="ledgers",
            note_type="note",
            content=note_content("Status: second entry appended."),
        )
    )
    await semantic_service.index_entity(entity)
    second_sync = await repository.sync_entity_vectors_batch([entity.id])

    unchanged_texts = [
        "Ledger notes stay in plain Markdown",
        "Agents append to ledgers many times a day",
        "Large notes amplify indexing cost",
        "Indexing Pipeline",
        "Embedding Provider",
    ]
    reembedded = [
        text
        for text in provider.embedded_texts
        if any(unchanged in text for unchanged in unchanged_texts)
        and "second entry appended" not in text
    ]
    assert reembedded == []
    assert any("second entry appended" in text for text in provider.embedded_texts)
    assert second_sync.chunks_skipped > 0

    # The reused chunks now point at the recreated search rows, so a vector search for
    # unchanged text still resolves to the note.
    results = await semantic_service.search(
        SearchQuery(
            text="Large notes amplify indexing cost",
            retrieval_mode=SearchRetrievalMode.VECTOR,
        )
    )
    assert any(result.entity_id == entity.id for result in results)
