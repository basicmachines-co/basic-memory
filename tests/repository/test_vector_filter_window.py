"""A filtered vector search fills its candidate window instead of stopping short.

The adapter ranks by similarity alone and knows nothing of structured filters. A window
taken straight from that ranking and filtered afterwards can hold few admitted rows while
more sit just past it, so a page came back short although matches existed. The reader now
re-reads the window with a bounded geometric overfetch until it holds enough admitted rows,
the ranking is exhausted, or its tail has fallen below the similarity threshold.
"""

from collections.abc import Sequence
from typing import Any, cast, override
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.repository.search_index_row import SearchIndexKey, SearchIndexRow
from basic_memory.repository.search_query import PreparedSearchQuery
from basic_memory.repository.search_reader import (
    VECTOR_FILTER_SCAN_LIMIT,
    HydratedChunk,
    SemanticSearch,
)
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.search_trace import SearchTraceCollector
from basic_memory.schemas.search import SearchRetrievalMode
from tests.repository.test_hybrid_fusion import FakeFts, FakeRow, fake_vector_retrieval
from tests.repository.test_vector_threshold import fake_session_maker

FILTERED = PreparedSearchQuery(
    search_text="test", retrieval_mode=SearchRetrievalMode.VECTOR, file_path_prefix="notes"
)
UNFILTERED = PreparedSearchQuery(search_text="test", retrieval_mode=SearchRetrievalMode.VECTOR)


class AdmittingFts(FakeFts):
    """Answers a filter pass with exactly the candidates in ``admitted``."""

    def __init__(self, admitted: set[int]) -> None:
        super().__init__()
        self.admitted = admitted

    @override
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
        self.calls.append({"candidate_keys": list(candidate_keys or [])})
        return cast(
            list[SearchIndexRow],
            [
                FakeRow(id=row_id)
                for _type, row_id in candidate_keys or []
                if row_id in self.admitted
            ],
        )


def _ranking(count: int, *, top: float = 0.99, step: float = 0.01) -> list[HydratedChunk]:
    """Rows 1..count, nearest first, similarity falling by ``step`` per rank."""
    return [
        HydratedChunk(
            entity_id=row_id,
            chunk_key=f"entity:{row_id}:0",
            chunk_text=f"chunk {row_id}",
            similarity=top - (row_id - 1) * step,
        )
        for row_id in range(1, count + 1)
    ]


def _adapter(ranking: list[HydratedChunk]) -> AsyncMock:
    """A neighbour stage that hands back the top ``candidate_limit`` of a fixed ranking."""

    async def run(session: Any, embedding: Any, candidate_limit: int, *, trace: Any = None):
        return ranking[:candidate_limit]

    return AsyncMock(side_effect=run)


def _rows() -> AsyncMock:
    async def fetch(row_ids: list[int]) -> dict[SearchIndexKey, Any]:
        return {
            ("entity", row_id): FakeRow(id=row_id, file_path=f"row-{row_id}.md")
            for row_id in row_ids
        }

    return AsyncMock(side_effect=fetch)


def _semantic(fts: FakeFts, *, vector_k: int = 4, min_similarity: float = 0.0) -> SemanticSearch:
    return SemanticSearch(
        fake_session_maker(),
        ProjectScope.single(1),
        fts,
        fake_vector_retrieval(vector_k=vector_k, min_similarity=min_similarity),
    )


async def _search(
    semantic: SemanticSearch,
    adapter: AsyncMock,
    *,
    query: PreparedSearchQuery,
    limit: int = 2,
) -> list[int]:
    with (
        patch.object(semantic, "_run_vector_query", adapter),
        patch.object(semantic, "_fetch_search_index_rows_by_ids", _rows()),
    ):
        rows = await semantic.vector_only(query, limit=limit, offset=0)
    return [row.id for row in rows if row.id is not None]


def _windows(adapter: AsyncMock) -> list[int]:
    return [call.args[2] for call in adapter.await_args_list]


@pytest.mark.asyncio
async def test_the_window_widens_until_the_filter_has_admitted_enough_rows():
    """Twenty rejected neighbours in front of five admitted ones still yield a full page."""
    fts = AdmittingFts(admitted={21, 22, 23, 24, 25})
    adapter = _adapter(_ranking(25))

    # limit 2 with vector_k 4 sizes the window at 20 chunks, all of them rejected.
    found = await _search(_semantic(fts), adapter, query=FILTERED)

    assert found == [21, 22]
    assert _windows(adapter) == [20, 40]


@pytest.mark.asyncio
async def test_a_query_without_filters_reads_its_window_once():
    fts = AdmittingFts(admitted=set())
    adapter = _adapter(_ranking(25))

    found = await _search(_semantic(fts), adapter, query=UNFILTERED)

    assert found == [1, 2]
    assert _windows(adapter) == [20]
    assert fts.calls == []


@pytest.mark.asyncio
async def test_an_exhausted_ranking_ends_the_search_with_what_it_admitted():
    """When the adapter has nothing past the window, a short answer is the true answer."""
    fts = AdmittingFts(admitted={5})
    adapter = _adapter(_ranking(12))

    found = await _search(_semantic(fts), adapter, query=FILTERED)

    assert found == [5]
    # The first read came back short of its own window: nothing further exists.
    assert _windows(adapter) == [20]


@pytest.mark.asyncio
async def test_a_ranking_that_stops_growing_is_exhausted():
    """An adapter capped below the widened window cannot be asked forever."""
    fts = AdmittingFts(admitted=set())
    adapter = _adapter(_ranking(20))

    found = await _search(_semantic(fts), adapter, query=FILTERED)

    assert found == []
    assert _windows(adapter) == [20, 40]


@pytest.mark.asyncio
async def test_a_tail_below_the_threshold_ends_the_widening():
    """Nothing past a sub-threshold tail can qualify, whatever the filter would admit."""
    fts = AdmittingFts(admitted={30})
    # Similarities fall from 0.99 by 0.03 per rank: rank 20 sits at 0.42, under 0.5.
    adapter = _adapter(_ranking(40, step=0.03))

    found = await _search(_semantic(fts, min_similarity=0.5), adapter, query=FILTERED)

    assert found == []
    assert _windows(adapter) == [20]


@pytest.mark.asyncio
async def test_the_widening_is_bounded_by_the_scan_limit():
    fts = AdmittingFts(admitted=set())
    adapter = _adapter(_ranking(VECTOR_FILTER_SCAN_LIMIT + 10, step=0.0))

    found = await _search(_semantic(fts), adapter, query=FILTERED)

    assert found == []
    windows = _windows(adapter)
    assert windows[0] == 20
    assert windows[-1] == VECTOR_FILTER_SCAN_LIMIT
    assert all(
        later == min(earlier * 2, VECTOR_FILTER_SCAN_LIMIT)
        for earlier, later in zip(windows, windows[1:])
    )


@pytest.mark.asyncio
async def test_each_round_asks_the_filter_only_about_rows_that_exist():
    """The filter pass is bounded by the candidates, never a page of the whole match set."""
    fts = AdmittingFts(admitted={21, 22})
    adapter = _adapter(_ranking(25))

    await _search(_semantic(fts), adapter, query=FILTERED)

    asked = [sorted(row_id for _type, row_id in call["candidate_keys"]) for call in fts.calls]
    assert asked == [list(range(1, 21)), list(range(1, 26))]
