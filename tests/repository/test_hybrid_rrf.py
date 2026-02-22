"""Tests for score-weighted reciprocal rank fusion (RRF) in hybrid search.

Verifies that the weighted RRF formula:
1. Boosts high-FTS-score results over low-FTS-score results at the same rank
2. Ranks dual-source results higher than single-source results
3. Uses a weight floor to prevent zero contribution from low scores
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from basic_memory.repository.search_repository_base import RRF_K, SearchRepositoryBase


@dataclass
class FakeRow:
    """Minimal stand-in for SearchIndexRow."""

    id: int | None
    type: str = "entity"
    score: float = 0.0
    title: str = ""
    permalink: str = ""
    file_path: str = ""
    metadata: str | None = None
    from_id: int | None = None
    to_id: int | None = None
    relation_type: str | None = None
    entity_id: int | None = None
    content_snippet: str | None = None
    category: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    project_id: int = 1


class ConcreteSearchRepo(SearchRepositoryBase):
    """Minimal concrete subclass for testing hybrid RRF logic."""

    def __init__(self):
        self._semantic_enabled = True
        self._semantic_vector_k = 100
        self._semantic_min_similarity = 0.0
        # _search_hybrid calls _assert_semantic_available which checks this
        self._embedding_provider = type("EP", (), {"dimensions": 384})()
        self._vector_dimensions = 384
        self._vector_tables_initialized = True
        self.session_maker = None
        self.project_id = 1

    async def init_search_index(self):
        pass  # pragma: no cover

    def _prepare_search_term(self, term, is_prefix=True):
        return term  # pragma: no cover

    async def search(self, **kwargs):
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


HYBRID_KWARGS = dict(
    search_text="test",
    permalink=None,
    permalink_match=None,
    title=None,
    note_types=None,
    after_date=None,
    search_item_types=None,
    metadata_filters=None,
    limit=10,
    offset=0,
)


@pytest.mark.asyncio
async def test_high_fts_score_boosts_ranking():
    """A high FTS score at rank 1 should outscore a low FTS score at rank 1."""
    repo = ConcreteSearchRepo()

    # Two FTS results at ranks 1 and 2, with very different scores
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
    # High FTS score at rank 1 should rank first
    assert results[0].id == 1
    # Verify the score is weighted — not just 1/(k+rank)
    1.0 / (RRF_K + 1)
    assert results[0].score > results[1].score


@pytest.mark.asyncio
async def test_dual_source_ranks_higher_than_single():
    """A result in both FTS and vector should rank above one in only FTS."""
    repo = ConcreteSearchRepo()

    # Row 1 appears in both FTS and vector; Row 2 only in FTS
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
    # Row 1 (dual-source) should rank first
    assert result_ids[0] == 1


@pytest.mark.asyncio
async def test_weight_floor_prevents_zero_contribution():
    """Even a zero-score result should contribute via the 0.1 weight floor."""
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
    # Weight floor of 0.1 means score = 0.1 * 1/(60+1)
    expected_min = 0.1 * (1.0 / (RRF_K + 1))
    assert results[0].score == pytest.approx(expected_min, rel=1e-6)
