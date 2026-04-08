"""Semantic search service regression tests for local SQLite search."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from basic_memory.repository import EntityRepository
from basic_memory.repository.search_repository_base import VectorSyncBatchResult
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
    execute_query = AsyncMock()
    monkeypatch.setattr(repository, "sync_entity_vectors", sync_vectors)
    monkeypatch.setattr(repository, "execute_query", execute_query)

    await search_service.sync_entity_vectors(42)

    sync_vectors.assert_not_awaited()
    assert execute_query.await_count == 2


@pytest.mark.asyncio
async def test_semantic_vector_sync_resumes_when_embed_opt_out_removed(
    search_service, monkeypatch
):
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
    execute_query = AsyncMock()
    monkeypatch.setattr(repository, "sync_entity_vectors_batch", sync_batch)
    monkeypatch.setattr(repository, "execute_query", execute_query)

    result = await search_service.sync_entity_vectors_batch([41, 42])

    sync_batch.assert_awaited_once()
    assert sync_batch.await_args.args[0] == [42]
    assert result.entities_total == 2
    assert result.entities_synced == 1
    assert result.entities_skipped == 1
    assert execute_query.await_count == 2


@pytest.mark.asyncio
async def test_embed_opt_out_note_still_participates_in_fts(
    search_service, session_maker, test_project
):
    """Per-note semantic opt-out should not remove the note from FTS search."""
    entity_repo = EntityRepository(session_maker, project_id=test_project.id)
    entity = await entity_repo.create(
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
        }
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
            entities_synced=1,
            entities_failed=0,
            entities_skipped=1,
        )
    )
    monkeypatch.setattr(search_service, "_purge_stale_search_rows", purge_stale_rows)
    monkeypatch.setattr(search_service, "sync_entity_vectors_batch", sync_batch)

    stats = await search_service.reindex_vectors()

    purge_stale_rows.assert_awaited_once()
    sync_batch.assert_awaited_once_with([41, 42], progress_callback=None)
    assert stats == {
        "total_entities": 2,
        "embedded": 1,
        "skipped": 1,
        "errors": 0,
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
            ),
            VectorSyncBatchResult(
                entities_total=1,
                entities_synced=1,
                entities_failed=0,
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
        call for call in sync_batch.await_args_list if call.kwargs.get("progress_callback") is not None
    ]
    assert len(progress_callback_calls) == 1
    assert progress_callback_calls[0].args[0] == [42]
    assert progress_callback_calls[0].kwargs["progress_callback"] is progress_callback
    assert result.entities_total == 2
    assert result.entities_synced == 2
    assert result.entities_failed == 0
    assert result.entities_skipped == 0
