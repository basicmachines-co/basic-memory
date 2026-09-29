"""Tests for semantic_min_similarity threshold filtering in vector search."""

from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from basic_memory.repository.search_query import PreparedSearchQuery
from basic_memory.repository.search_reader import (
    SMALL_NOTE_CONTENT_LIMIT,
    TOP_CHUNKS_PER_RESULT,
    HydratedChunk,
    SemanticSearch,
)
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.schemas.search import SearchRetrievalMode
from tests.repository.test_hybrid_fusion import FakeFts, fake_vector_retrieval


@dataclass
class FakeRow:
    """Minimal stand-in for SearchIndexRow in threshold tests."""

    id: int
    type: str = "entity"
    score: float = 0.0
    matched_chunk_text: str | None = None
    content_snippet: str | None = None


def _make_vector_rows(scores: list[float]) -> list[HydratedChunk]:
    """One hydrated chunk per search row, ranked at the given similarity."""
    return [
        HydratedChunk(
            entity_id=index,
            chunk_key=f"entity:{index}:0",
            chunk_text=f"chunk text for entity:{index}:0",
            similarity=score,
        )
        for index, score in enumerate(scores)
    ]


def fake_session_maker() -> Any:
    """A session factory for the hydration step; the stubbed stages never touch it."""

    @asynccontextmanager
    async def session():
        yield AsyncMock()

    return session


def vector_semantic(*, min_similarity: float = 0.0, fts: FakeFts | None = None) -> SemanticSearch:
    """A vector pipeline with a stubbed adapter, ready for its neighbour stage to be patched."""
    return SemanticSearch(
        fake_session_maker(),
        ProjectScope.single(1),
        fts or FakeFts(),
        fake_vector_retrieval(min_similarity=min_similarity),
    )


VECTOR_QUERY = PreparedSearchQuery(search_text="test", retrieval_mode=SearchRetrievalMode.VECTOR)


async def run_vector_only(
    semantic: SemanticSearch,
    vector_rows: list[HydratedChunk],
    fetch_rows: AsyncMock,
    *,
    query: PreparedSearchQuery = VECTOR_QUERY,
    limit: int = 10,
    offset: int = 0,
) -> list[Any]:
    """Run vector-only search with the neighbour and row-fetch stages stubbed."""
    with (
        patch.object(
            semantic, "_run_vector_query", new_callable=AsyncMock, return_value=vector_rows
        ),
        patch.object(semantic, "_fetch_search_index_rows_by_ids", fetch_rows),
    ):
        return await semantic.vector_only(query, limit=limit, offset=offset)


def _index_rows(count: int) -> AsyncMock:
    return AsyncMock(return_value={("entity", i): FakeRow(id=i) for i in range(count)})


@pytest.mark.asyncio
async def test_threshold_zero_returns_all():
    """With threshold=0.0 (default), all results pass through."""
    semantic = vector_semantic(min_similarity=0.0)

    results = await run_vector_only(semantic, _make_vector_rows([0.9, 0.5, 0.3]), _index_rows(3))

    assert len(results) == 3


@pytest.mark.asyncio
async def test_threshold_filters_low_scores():
    """Results below the threshold are excluded."""
    semantic = vector_semantic(min_similarity=0.6)

    # Scores: 0.9 (pass), 0.5 (fail), 0.3 (fail). Only entity_0 reaches the row fetch.
    results = await run_vector_only(semantic, _make_vector_rows([0.9, 0.5, 0.3]), _index_rows(1))

    assert len(results) == 1


@pytest.mark.asyncio
async def test_threshold_returns_empty_when_all_below():
    """All results below threshold → empty list, no DB fetch."""
    semantic = vector_semantic(min_similarity=0.8)
    fetch_rows = AsyncMock()

    results = await run_vector_only(semantic, _make_vector_rows([0.5, 0.4, 0.3]), fetch_rows)

    assert results == []
    # Should short-circuit before fetching search_index rows
    fetch_rows.assert_not_called()


@pytest.mark.asyncio
async def test_per_query_min_similarity_overrides_configured_default():
    """Per-query min_similarity takes precedence over the configured default."""
    # The configured default would filter out 0.5 and 0.3
    semantic = vector_semantic(min_similarity=0.6)

    # Override to 0.0 → all results pass through despite the configured 0.6
    results = await run_vector_only(
        semantic,
        _make_vector_rows([0.9, 0.5, 0.3]),
        _index_rows(3),
        query=replace(VECTOR_QUERY, min_similarity=0.0),
    )

    assert len(results) == 3


@pytest.mark.asyncio
async def test_per_query_min_similarity_tightens_threshold():
    """Per-query min_similarity=0.8 filters more aggressively than the configured default."""
    semantic = vector_semantic(min_similarity=0.0)

    # Override to 0.8 → only score=0.9 passes
    results = await run_vector_only(
        semantic,
        _make_vector_rows([0.9, 0.5, 0.3]),
        _index_rows(1),
        query=replace(VECTOR_QUERY, min_similarity=0.8),
    )

    assert len(results) == 1
    assert results[0].id == 0


@pytest.mark.asyncio
async def test_matched_chunk_text_populated_on_vector_results():
    """Vector search results carry the matched chunk text from the best-matching chunk."""
    semantic = vector_semantic()

    results = await run_vector_only(semantic, _make_vector_rows([0.9, 0.7]), _index_rows(2))

    assert len(results) == 2
    # Results are sorted by score descending, so id=0 (0.9) first, id=1 (0.7) second
    # Each entity has only 1 chunk and no content_snippet, so chunk text is used directly
    assert results[0].matched_chunk_text == "chunk text for entity:0:0"
    assert results[1].matched_chunk_text == "chunk text for entity:1:0"


def _make_multi_chunk_vector_rows(si_id: int, scores: list[float]) -> list[HydratedChunk]:
    """Several chunks of one search row, each at its own similarity."""
    return [
        HydratedChunk(
            entity_id=si_id,
            chunk_key=f"entity:{si_id}:{chunk_index}",
            chunk_text=f"chunk-{chunk_index} (sim={score})",
            similarity=score,
        )
        for chunk_index, score in enumerate(scores)
    ]


@pytest.mark.asyncio
async def test_top_n_chunks_joined_in_matched_chunk_text():
    """Large note with 7 chunks: top 5 by similarity are joined with separator."""
    semantic = vector_semantic()
    chunk_scores = [0.6, 0.9, 0.4, 0.8, 0.75, 0.3, 0.85]
    # content_snippet exceeds SMALL_NOTE_CONTENT_LIMIT → top-N chunks path
    large_content = "x" * (SMALL_NOTE_CONTENT_LIMIT + 1)

    results = await run_vector_only(
        semantic,
        _make_multi_chunk_vector_rows(si_id=0, scores=chunk_scores),
        AsyncMock(return_value={("entity", 0): FakeRow(id=0, content_snippet=large_content)}),
    )

    assert len(results) == 1
    text = results[0].matched_chunk_text
    assert text is not None

    # Top 5 chunks by similarity: 0.9, 0.85, 0.8, 0.75, 0.6 (0.4 and 0.3 excluded)
    parts = text.split("\n---\n")
    assert len(parts) == TOP_CHUNKS_PER_RESULT
    assert parts[0] == "chunk-1 (sim=0.9)"
    assert parts[1] == "chunk-6 (sim=0.85)"
    assert parts[2] == "chunk-3 (sim=0.8)"
    assert parts[3] == "chunk-4 (sim=0.75)"
    assert parts[4] == "chunk-0 (sim=0.6)"


@pytest.mark.asyncio
async def test_small_note_returns_full_content_as_matched_chunk():
    """Small note (content_snippet under limit) returns full content instead of chunks."""
    semantic = vector_semantic()
    small_content = "This is a short note with all the important details."
    assert len(small_content) <= SMALL_NOTE_CONTENT_LIMIT

    results = await run_vector_only(
        semantic,
        _make_vector_rows([0.9]),
        AsyncMock(return_value={("entity", 0): FakeRow(id=0, content_snippet=small_content)}),
    )

    assert len(results) == 1
    # Full content returned instead of the chunk text
    assert results[0].matched_chunk_text == small_content


@pytest.mark.asyncio
async def test_large_note_returns_chunks_not_full_content():
    """Large note (content_snippet over limit) returns top-N chunks, not full content."""
    semantic = vector_semantic()
    large_content = "x" * (SMALL_NOTE_CONTENT_LIMIT + 500)

    results = await run_vector_only(
        semantic,
        _make_vector_rows([0.9]),
        AsyncMock(return_value={("entity", 0): FakeRow(id=0, content_snippet=large_content)}),
    )

    assert len(results) == 1
    # Should use chunk text, not the full content
    assert results[0].matched_chunk_text == "chunk text for entity:0:0"
    assert results[0].matched_chunk_text != large_content


@pytest.mark.asyncio
async def test_unparseable_chunk_key_names_no_search_row():
    """A chunk whose key does not spell a search row is skipped rather than ranked."""
    semantic = vector_semantic()
    rows = [
        HydratedChunk(entity_id=0, chunk_key="entity:0:0", chunk_text="good", similarity=0.9),
        HydratedChunk(entity_id=0, chunk_key="garbage", chunk_text="bad", similarity=0.95),
    ]

    results = await run_vector_only(semantic, rows, _index_rows(1))

    assert [row.id for row in results] == [0]
    assert results[0].matched_chunk_text == "good"
