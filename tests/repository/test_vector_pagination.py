"""Tests for vector search pagination score ordering.

Verifies that page 1 results always have scores >= page 2 results,
which requires a sufficiently large candidate_limit multiplier.
"""

from unittest.mock import AsyncMock

import pytest

from basic_memory.repository.search_reader import HydratedChunk
from tests.repository.test_vector_threshold import FakeRow, run_vector_only, vector_semantic


def _make_descending_vector_rows(count: int) -> list[HydratedChunk]:
    """Build vector rows with similarity descending from 0.95 in steps of 0.01."""
    return [
        HydratedChunk(
            entity_id=index,
            chunk_key=f"entity:{index}:0",
            chunk_text=f"chunk text {index}",
            similarity=0.95 - (index * 0.01),
        )
        for index in range(count)
    ]


@pytest.mark.asyncio
async def test_page1_scores_gte_page2_scores():
    """Page 1 minimum score must be >= page 2 maximum score."""
    semantic = vector_semantic()
    # 20 results with descending scores
    fake_rows = _make_descending_vector_rows(20)
    fake_index_rows = {("entity", i): FakeRow(id=i) for i in range(20)}

    async def run_page(offset: int, limit: int):
        return await run_vector_only(
            semantic,
            fake_rows,
            AsyncMock(return_value=fake_index_rows),
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
