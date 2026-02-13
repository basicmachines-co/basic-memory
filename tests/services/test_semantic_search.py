"""Semantic search service regression tests for local SQLite search."""

import pytest

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
