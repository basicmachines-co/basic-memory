"""Tests for score-based fusion in hybrid search.

Verifies that the fusion formula (max + FUSION_BONUS * min):
1. Preserves FTS score differentiation (high-score > low-score)
2. Ranks dual-source results higher than single-source results
3. Produces zero fused score when the source score is zero
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.search_index_row import SearchIndexKey, SearchIndexRow
from basic_memory.repository.search_query import PreparedSearchQuery
from basic_memory.repository.search_reader import (
    FUSION_BONUS,
    SemanticSearch,
    VectorRetrieval,
)
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.search_trace import SearchTraceCollector
from basic_memory.repository.semantic_vector_index import SemanticVectorIndex
from basic_memory.schemas.search import SearchRetrievalMode


@dataclass
class FakeRow:
    """Minimal stand-in for SearchIndexRow."""

    id: int | None
    type: str = "entity"
    score: float = 0.0
    title: str = ""
    permalink: str = ""
    content_snippet: str | None = None
    file_path: str = ""
    metadata: str | None = None
    from_id: int | None = None
    to_id: int | None = None
    relation_type: str | None = None
    entity_id: int | None = None
    category: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    project_id: int = 1
    matched_chunk_text: str | None = None


class FakeFts:
    """An ``FtsBackend`` that answers every pass with fixed rows and records what it was asked.

    ``rows`` is the answer, or a function of the requested ``limit`` when a test needs
    the lexical leg to widen with the candidate window.
    """

    def __init__(self, rows: Sequence[Any] | Callable[[int], Sequence[Any]] = ()) -> None:
        if isinstance(rows, Sequence):
            fixed = list(rows)
            self.answer: Callable[[int], Sequence[Any]] = lambda _limit: fixed
        else:
            self.answer = rows
        self.queries: list[PreparedSearchQuery] = []
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        scope: ProjectScope,
        query: PreparedSearchQuery,
        *,
        limit: int,
        offset: int,
        allow_relaxed: bool = False,
        session: AsyncSession | None = None,
        candidate_keys: Sequence[SearchIndexKey] | None = None,
        trace: SearchTraceCollector | None = None,
    ) -> list[SearchIndexRow]:
        self.queries.append(query)
        self.calls.append(
            {
                "limit": limit,
                "offset": offset,
                "allow_relaxed": allow_relaxed,
                "candidate_keys": candidate_keys,
            }
        )
        return cast(list[SearchIndexRow], list(self.answer(limit)))

    async def count(
        self,
        scope: ProjectScope,
        query: PreparedSearchQuery,
        *,
        allow_relaxed: bool = False,
    ) -> int:
        return len(self.answer(0))


def fake_vector_retrieval(
    *,
    vector_k: int = 100,
    min_similarity: float = 0.0,
    embed_query: AsyncMock | None = None,
) -> VectorRetrieval:
    """A semantic stack whose adapter is never consulted: tests stub the neighbour stage."""
    provider = type(
        "EP",
        (),
        {
            "model_name": "fake",
            "dimensions": 384,
            "embed_query": embed_query or AsyncMock(return_value=[0.0] * 384),
            "embed_documents": AsyncMock(return_value=[]),
            "runtime_log_attrs": lambda self: {},
        },
    )()
    return VectorRetrieval(
        index=cast(SemanticVectorIndex, object()),
        index_name="sqlite-vec",
        embedding_provider=cast(EmbeddingProvider, provider),
        embedding_model="fake:384",
        vector_k=vector_k,
        min_similarity=min_similarity,
    )


HYBRID_QUERY = PreparedSearchQuery(search_text="test", retrieval_mode=SearchRetrievalMode.HYBRID)


async def fuse(
    fts_results: list[Any],
    vector_results: list[Any],
    *,
    query: PreparedSearchQuery = HYBRID_QUERY,
) -> list[SearchIndexRow]:
    """Run hybrid with both legs answering fixed rows, so only fusion is under test."""
    semantic = SemanticSearch(
        cast(Any, None), ProjectScope.single(1), FakeFts(fts_results), fake_vector_retrieval()
    )
    with patch.object(semantic, "vector_only", new_callable=AsyncMock, return_value=vector_results):
        return await semantic.hybrid(query, limit=10, offset=0)


@pytest.mark.asyncio
async def test_high_fts_score_boosts_ranking():
    """FTS-only: a high normalized score should outscore a low normalized score."""
    high_score_row = FakeRow(id=1, score=10.0, title="high")
    low_score_row = FakeRow(id=2, score=0.5, title="low")

    # No vector results — isolate FTS weighting behavior
    results = await fuse([high_score_row, low_score_row], [])

    assert len(results) == 2
    # After normalization: id=1 → 1.0, id=2 → 0.05
    assert results[0].id == 1
    assert results[0].score == pytest.approx(1.0, rel=1e-6)
    assert results[1].score == pytest.approx(0.05, rel=1e-6)


@pytest.mark.asyncio
async def test_dual_source_ranks_higher_than_single():
    """A result in both FTS and vector should rank above single-source results."""
    # Row 1 in both (fts=5.0→norm 1.0, vec=0.9), Row 2 FTS-only (fts=5.0→norm 1.0),
    # Row 3 vec-only (0.8)
    fts_results = [
        FakeRow(id=1, score=5.0, title="both"),
        FakeRow(id=2, score=5.0, title="fts-only"),
    ]
    vector_results = [
        FakeRow(id=1, score=0.9, title="both"),
        FakeRow(id=3, score=0.8, title="vec-only"),
    ]

    results = await fuse(fts_results, vector_results)

    result_ids = [r.id for r in results]
    # Row 1 (dual-source) should rank first, then Row 2 (FTS 1.0), then Row 3 (vec 0.8)
    assert result_ids == [1, 2, 3]

    # Row 1: max(0.9, 1.0) + 0.3 * min(0.9, 1.0) = 1.0 + 0.27 = 1.27
    assert results[0].score == pytest.approx(1.0 + FUSION_BONUS * 0.9, rel=1e-6)
    # Row 2: FTS-only, fused = max(0, 1.0) + 0.3 * min(0, 1.0) = 1.0
    assert results[1].score == pytest.approx(1.0, rel=1e-6)
    # Row 3: vec-only, fused = max(0.8, 0) + 0.3 * min(0.8, 0) = 0.8
    assert results[2].score == pytest.approx(0.8, rel=1e-6)


@pytest.mark.asyncio
async def test_zero_score_produces_zero_fused():
    """A zero-score FTS result with no vector match produces a zero fused score."""
    results = await fuse([FakeRow(id=1, score=0.0, title="zero-score")], [])

    assert len(results) == 1
    # Zero FTS score, no vector → fused = max(0, 0) + 0.3 * min(0, 0) = 0.0
    assert results[0].score == pytest.approx(0.0, rel=1e-6)


@pytest.mark.asyncio
async def test_cross_type_id_collision_keeps_both_results():
    """An entity and a relation sharing the same numeric id stay distinct (#982).

    search_index row types have independent id sequences, so fusing on a bare
    row id merged unrelated rows into one result and dropped the other.
    """
    fts_results = [FakeRow(id=1, type="entity", score=5.0, title="entity-row")]
    vector_results = [FakeRow(id=1, type="relation", score=0.8, title="relation-row")]

    results = await fuse(fts_results, vector_results)

    assert {(r.type, r.id) for r in results} == {("entity", 1), ("relation", 1)}
    # Single-source scores must not earn the dual-source fusion bonus across types.
    entity_result = next(r for r in results if r.type == "entity")
    relation_result = next(r for r in results if r.type == "relation")
    assert entity_result.score == pytest.approx(1.0, rel=1e-6)
    assert relation_result.score == pytest.approx(0.8, rel=1e-6)


@pytest.mark.asyncio
async def test_fts_only_result_does_not_copy_content_into_matched_chunk():
    """FTS-only hits use the API content preview instead of a second full-note field."""
    content = "This is the full note content with the answer we need to find."

    results = await fuse([FakeRow(id=1, score=5.0, title="fts-hit", content_snippet=content)], [])

    assert len(results) == 1
    assert results[0].matched_chunk_text is None
    assert results[0].content_snippet == content


@pytest.mark.asyncio
async def test_fts_only_result_with_null_content_keeps_null_matched_chunk():
    """FTS-only results with no content_snippet should keep matched_chunk_text as None."""
    results = await fuse([FakeRow(id=1, score=5.0, title="fts-hit", content_snippet=None)], [])

    assert len(results) == 1
    assert results[0].matched_chunk_text is None


@pytest.mark.asyncio
async def test_dual_source_result_keeps_vector_matched_chunk():
    """Dual-source results should keep matched_chunk_text from vector search, not overwrite."""
    content = "Full note content from FTS."
    vector_chunk = "Specific chunk matched by vector search."
    fts_results = [
        FakeRow(id=1, score=5.0, title="both", content_snippet=content),
    ]
    vector_results = [
        FakeRow(
            id=1,
            score=0.9,
            title="both",
            content_snippet=content,
            matched_chunk_text=vector_chunk,
        ),
    ]

    results = await fuse(fts_results, vector_results)

    assert len(results) == 1
    # Vector result overwrites the FTS row in rows_by_key, so matched_chunk_text is preserved
    assert results[0].matched_chunk_text == vector_chunk


@pytest.mark.asyncio
async def test_hybrid_fts_leg_runs_in_fts_mode_with_relaxation():
    """The lexical leg is the same prepared query in FTS mode, allowed to relax."""
    fts = FakeFts([FakeRow(id=1, score=5.0)])
    semantic = SemanticSearch(cast(Any, None), ProjectScope.single(1), fts, fake_vector_retrieval())

    with patch.object(semantic, "vector_only", new_callable=AsyncMock, return_value=[]):
        await semantic.hybrid(HYBRID_QUERY, limit=10, offset=0)

    assert [query.retrieval_mode for query in fts.queries] == [SearchRetrievalMode.FTS]
    assert fts.queries[0].search_text == "test"
    assert fts.calls[0]["allow_relaxed"] is True
