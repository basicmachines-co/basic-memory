"""Tests for vector search pagination score ordering.

Verifies that page 1 results always have scores >= page 2 results,
which requires a sufficiently large candidate_limit multiplier.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from basic_memory.repository.search_repository_base import SearchRepositoryBase


@dataclass
class FakeRow:
    """Minimal stand-in for SearchIndexRow in pagination tests."""

    id: int
    type: str = "entity"
    score: float = 0.0


class ConcreteSearchRepo(SearchRepositoryBase):
    """Minimal concrete subclass for testing base class pagination logic."""

    def __init__(self):
        self._semantic_enabled = True
        self._semantic_vector_k = 100
        self._semantic_min_similarity = 0.0
        self._embedding_provider = None
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
        return 1.0 / (1.0 + max(distance, 0.0))


@asynccontextmanager
async def fake_scoped_session(session_maker):
    yield AsyncMock()


def _make_descending_vector_rows(count: int) -> list[dict]:
    """Build vector rows with scores descending from ~1.0 to ~0.5."""
    rows = []
    for i in range(count):
        # Similarity decreases linearly: 0.95, 0.94, 0.93, ...
        similarity = 0.95 - (i * 0.01)
        distance = (1.0 / similarity) - 1.0
        rows.append({"chunk_key": f"entity:{i}:0", "best_distance": distance})
    return rows


@pytest.mark.asyncio
async def test_page1_scores_gte_page2_scores():
    """Page 1 minimum score must be >= page 2 maximum score."""
    repo = ConcreteSearchRepo()

    # 20 results with descending scores
    fake_rows = _make_descending_vector_rows(20)

    mock_embed = AsyncMock(return_value=[0.0] * 384)
    repo._embedding_provider = type("EP", (), {"embed_query": mock_embed, "dimensions": 384})()

    fake_index_rows = {i: FakeRow(id=i) for i in range(20)}

    async def run_page(offset, limit):
        with (
            patch(
                "basic_memory.repository.search_repository_base.db.scoped_session",
                fake_scoped_session,
            ),
            patch.object(repo, "_ensure_vector_tables", new_callable=AsyncMock),
            patch.object(repo, "_prepare_vector_session", new_callable=AsyncMock),
            patch.object(repo, "_run_vector_query", new_callable=AsyncMock, return_value=fake_rows),
            patch.object(
                repo,
                "_fetch_search_index_rows_by_ids",
                new_callable=AsyncMock,
                return_value=fake_index_rows,
            ),
        ):
            return await repo._search_vector_only(
                search_text="test",
                permalink=None,
                permalink_match=None,
                title=None,
                note_types=None,
                after_date=None,
                search_item_types=None,
                metadata_filters=None,
                limit=limit,
                offset=offset,
            )

    page1 = await run_page(offset=0, limit=10)
    page2 = await run_page(offset=10, limit=10)

    assert len(page1) == 10
    assert len(page2) == 10

    page1_min = min(r.score for r in page1)
    page2_max = max(r.score for r in page2)

    assert page1_min >= page2_max, (
        f"Score inversion: page 1 min ({page1_min:.4f}) < page 2 max ({page2_max:.4f})"
    )
