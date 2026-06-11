"""Tests for score-based fusion in hybrid search.

Verifies that the fusion formula (max + FUSION_BONUS * min):
1. Preserves FTS score differentiation (high-score > low-score)
2. Ranks dual-source results higher than single-source results
3. Produces zero fused score when the source score is zero
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, cast
from unittest.mock import AsyncMock, patch

import pytest

from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_repository_base import FUSION_BONUS, SearchRepositoryBase
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode


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


class ConcreteSearchRepo(SearchRepositoryBase):
    """Minimal concrete subclass for testing hybrid fusion logic."""

    def __init__(self):
        self._semantic_enabled = True
        self._semantic_vector_k = 100
        self._semantic_min_similarity = 0.0
        # _search_hybrid calls _assert_semantic_available which checks this
        self._embedding_provider = _fake_embedding_provider()
        self._vector_dimensions = 384
        self._vector_tables_initialized = True
        self.session_maker = None
        self.project_id = 1

    async def init_search_index(self):
        pass  # pragma: no cover

    def _prepare_search_term(self, term, is_prefix=True):
        return term  # pragma: no cover

    async def search(
        self,
        search_text: Optional[str] = None,
        permalink: Optional[str] = None,
        permalink_match: Optional[str] = None,
        title: Optional[str] = None,
        note_types: Optional[list[str]] = None,
        after_date: Optional[datetime] = None,
        search_item_types: Optional[list[SearchItemType]] = None,
        categories: Optional[list[str]] = None,
        metadata_filters: Optional[dict[str, Any]] = None,
        retrieval_mode: SearchRetrievalMode = SearchRetrievalMode.FTS,
        min_similarity: Optional[float] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[SearchIndexRow]:
        return []  # pragma: no cover

    async def _ensure_vector_tables(self):
        pass  # pragma: no cover

    async def _run_vector_query(self, session, query_embedding, candidate_limit):
        return []  # pragma: no cover

    async def _write_embeddings(self, session, jobs, embeddings):
        pass  # pragma: no cover

    async def _delete_entity_chunks(self, session, entity_id):
        pass  # pragma: no cover

    async def _delete_stale_chunks(self, session, stale_ids, entity_id):
        pass  # pragma: no cover

    async def _update_timestamp_sql(self):
        return "CURRENT_TIMESTAMP"  # pragma: no cover

    def _distance_to_similarity(self, distance: float) -> float:
        return 1.0 / (1.0 + max(distance, 0.0))  # pragma: no cover


def _fake_embedding_provider() -> EmbeddingProvider:
    return cast(
        EmbeddingProvider,
        type(
            "EP",
            (),
            {
                "model_name": "fake",
                "dimensions": 384,
                "embed_query": AsyncMock(return_value=[0.0] * 384),
                "embed_documents": AsyncMock(return_value=[]),
                "runtime_log_attrs": lambda self: {},
            },
        )(),
    )


HYBRID_KWARGS: dict[str, Any] = dict(
    search_text="test",
    permalink=None,
    permalink_match=None,
    title=None,
    note_types=None,
    after_date=None,
    search_item_types=None,
    categories=None,
    metadata_filters=None,
    limit=10,
    offset=0,
)


def _hybrid_kwargs(**overrides: Any) -> dict[str, Any]:
    """Return HYBRID_KWARGS with overrides applied, typed as dict[str, Any].

    Keeps the splat into the keyword-only _search_hybrid signature type-clean.
    """
    merged: dict[str, Any] = {**HYBRID_KWARGS, **overrides}
    return merged


@pytest.mark.asyncio
async def test_entity_boost_promotes_matching_doc_when_enabled():
    """With entity boost enabled, an entity-matching doc outranks a higher-similarity
    non-matching doc.

    Reproduces the #951 cross-conversation confusion: a generic same-topic document
    (higher raw similarity) initially outranks the gold doc whose title names the
    queried entity. Enabling the boost flips the order.
    """
    repo = ConcreteSearchRepo()
    repo._entity_boost_enabled = True
    repo._entity_boost_weight = 0.15
    repo._entity_boost_max_terms = 3

    # Row 1: generic hobbies doc from the wrong conversation, higher vector similarity.
    # Row 2: the gold doc whose title names the queried entity "Joanna".
    fts_results = []
    vector_results = [
        FakeRow(id=1, score=0.80, title="Hobbies and pastimes"),
        FakeRow(id=2, score=0.72, title="Joanna profile"),
    ]

    with (
        patch.object(repo, "search", new_callable=AsyncMock, return_value=fts_results),
        patch.object(
            repo, "_search_vector_only", new_callable=AsyncMock, return_value=vector_results
        ),
    ):
        results = await repo._search_hybrid(
            **_hybrid_kwargs(search_text="What are Joanna's hobbies?")
        )

    # Boost: row 2 -> 0.72 * 1.15 = 0.828 > row 1's 0.80
    assert [r.id for r in results] == [2, 1]
    assert results[0].score == pytest.approx(0.72 * 1.15, rel=1e-6)
    assert results[1].score == pytest.approx(0.80, rel=1e-6)


@pytest.mark.asyncio
async def test_entity_boost_disabled_preserves_ordering():
    """With entity boost disabled (default), ordering matches pure similarity."""
    repo = ConcreteSearchRepo()
    # Defaults from the base class keep boosting off; assert explicitly.
    assert repo._entity_boost_enabled is False

    fts_results = []
    vector_results = [
        FakeRow(id=1, score=0.80, title="Hobbies and pastimes"),
        FakeRow(id=2, score=0.72, title="Joanna profile"),
    ]

    with (
        patch.object(repo, "search", new_callable=AsyncMock, return_value=fts_results),
        patch.object(
            repo, "_search_vector_only", new_callable=AsyncMock, return_value=vector_results
        ),
    ):
        results = await repo._search_hybrid(
            **_hybrid_kwargs(search_text="What are Joanna's hobbies?")
        )

    # No boost: original similarity order is preserved, scores unchanged.
    assert [r.id for r in results] == [1, 2]
    assert results[0].score == pytest.approx(0.80, rel=1e-6)
    assert results[1].score == pytest.approx(0.72, rel=1e-6)


@pytest.mark.asyncio
async def test_entity_boost_promotes_doc_into_limited_window():
    """Boosting runs before the limit cut, so a matching doc ranked below the cutoff
    can be promoted into the returned window."""
    repo = ConcreteSearchRepo()
    repo._entity_boost_enabled = True
    repo._entity_boost_weight = 0.6
    repo._entity_boost_max_terms = 3

    fts_results = []
    # Three non-matching docs above the gold doc, which matches "Anthony".
    vector_results = [
        FakeRow(id=1, score=0.90, title="conversation six"),
        FakeRow(id=2, score=0.85, title="conversation one"),
        FakeRow(id=3, score=0.60, title="Anthony introduces himself"),
    ]

    with (
        patch.object(repo, "search", new_callable=AsyncMock, return_value=fts_results),
        patch.object(
            repo, "_search_vector_only", new_callable=AsyncMock, return_value=vector_results
        ),
    ):
        results = await repo._search_hybrid(
            **_hybrid_kwargs(search_text="Who is Anthony?", limit=1)
        )

    # Gold doc boost: 0.60 * 1.6 = 0.96 > row 1's 0.90, so it is promoted into the
    # top-1 window even though it was ranked third before boosting.
    assert len(results) == 1
    assert results[0].id == 3


@pytest.mark.asyncio
async def test_high_fts_score_boosts_ranking():
    """FTS-only: a high normalized score should outscore a low normalized score."""
    repo = ConcreteSearchRepo()

    # Two FTS results with very different scores
    high_score_row = FakeRow(id=1, score=10.0, title="high")
    low_score_row = FakeRow(id=2, score=0.5, title="low")
    fts_results = [high_score_row, low_score_row]

    # No vector results — isolate FTS weighting behavior
    vector_results = []

    with (
        patch.object(
            repo,
            "search",
            new_callable=AsyncMock,
            return_value=fts_results,
        ),
        patch.object(
            repo,
            "_search_vector_only",
            new_callable=AsyncMock,
            return_value=vector_results,
        ),
    ):
        results = await repo._search_hybrid(**HYBRID_KWARGS)

    assert len(results) == 2
    # After normalization: id=1 → 1.0, id=2 → 0.05
    assert results[0].id == 1
    assert results[0].score == pytest.approx(1.0, rel=1e-6)
    assert results[1].score == pytest.approx(0.05, rel=1e-6)


@pytest.mark.asyncio
async def test_dual_source_ranks_higher_than_single():
    """A result in both FTS and vector should rank above single-source results."""
    repo = ConcreteSearchRepo()

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

    with (
        patch.object(repo, "search", new_callable=AsyncMock, return_value=fts_results),
        patch.object(
            repo, "_search_vector_only", new_callable=AsyncMock, return_value=vector_results
        ),
    ):
        results = await repo._search_hybrid(**HYBRID_KWARGS)

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
    repo = ConcreteSearchRepo()

    # FTS result with score 0.0
    fts_results = [FakeRow(id=1, score=0.0, title="zero-score")]
    vector_results = []

    with (
        patch.object(repo, "search", new_callable=AsyncMock, return_value=fts_results),
        patch.object(
            repo, "_search_vector_only", new_callable=AsyncMock, return_value=vector_results
        ),
    ):
        results = await repo._search_hybrid(**HYBRID_KWARGS)

    assert len(results) == 1
    # Zero FTS score, no vector → fused = max(0, 0) + 0.3 * min(0, 0) = 0.0
    assert results[0].score == pytest.approx(0.0, rel=1e-6)


@pytest.mark.asyncio
async def test_cross_type_id_collision_keeps_both_results():
    """An entity and a relation sharing the same numeric id stay distinct (#982).

    search_index row types have independent id sequences, so fusing on a bare
    row id merged unrelated rows into one result and dropped the other.
    """
    repo = ConcreteSearchRepo()

    fts_results = [FakeRow(id=1, type="entity", score=5.0, title="entity-row")]
    vector_results = [FakeRow(id=1, type="relation", score=0.8, title="relation-row")]

    with (
        patch.object(repo, "search", new_callable=AsyncMock, return_value=fts_results),
        patch.object(
            repo, "_search_vector_only", new_callable=AsyncMock, return_value=vector_results
        ),
    ):
        results = await repo._search_hybrid(**HYBRID_KWARGS)

    assert {(r.type, r.id) for r in results} == {("entity", 1), ("relation", 1)}
    # Single-source scores must not earn the dual-source fusion bonus across types.
    entity_result = next(r for r in results if r.type == "entity")
    relation_result = next(r for r in results if r.type == "relation")
    assert entity_result.score == pytest.approx(1.0, rel=1e-6)
    assert relation_result.score == pytest.approx(0.8, rel=1e-6)


@pytest.mark.asyncio
async def test_fts_only_result_gets_matched_chunk_from_content_snippet():
    """FTS-only results should have matched_chunk_text populated from content_snippet."""
    repo = ConcreteSearchRepo()

    content = "This is the full note content with the answer we need to find."
    fts_results = [
        FakeRow(id=1, score=5.0, title="fts-hit", content_snippet=content),
    ]
    vector_results = []

    with (
        patch.object(repo, "search", new_callable=AsyncMock, return_value=fts_results),
        patch.object(
            repo, "_search_vector_only", new_callable=AsyncMock, return_value=vector_results
        ),
    ):
        results = await repo._search_hybrid(**HYBRID_KWARGS)

    assert len(results) == 1
    assert results[0].matched_chunk_text == content


@pytest.mark.asyncio
async def test_fts_only_result_with_null_content_keeps_null_matched_chunk():
    """FTS-only results with no content_snippet should keep matched_chunk_text as None."""
    repo = ConcreteSearchRepo()

    fts_results = [
        FakeRow(id=1, score=5.0, title="fts-hit", content_snippet=None),
    ]
    vector_results = []

    with (
        patch.object(repo, "search", new_callable=AsyncMock, return_value=fts_results),
        patch.object(
            repo, "_search_vector_only", new_callable=AsyncMock, return_value=vector_results
        ),
    ):
        results = await repo._search_hybrid(**HYBRID_KWARGS)

    assert len(results) == 1
    assert results[0].matched_chunk_text is None


@pytest.mark.asyncio
async def test_dual_source_result_keeps_vector_matched_chunk():
    """Dual-source results should keep matched_chunk_text from vector search, not overwrite."""
    repo = ConcreteSearchRepo()

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

    with (
        patch.object(repo, "search", new_callable=AsyncMock, return_value=fts_results),
        patch.object(
            repo, "_search_vector_only", new_callable=AsyncMock, return_value=vector_results
        ),
    ):
        results = await repo._search_hybrid(**HYBRID_KWARGS)

    assert len(results) == 1
    # Vector result overwrites the FTS row in rows_by_id, so matched_chunk_text is preserved
    assert results[0].matched_chunk_text == vector_chunk
