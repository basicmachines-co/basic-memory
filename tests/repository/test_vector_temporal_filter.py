"""Valid-time filters reach the semantic retrieval modes too (SPEC-82).

Vector and hybrid search apply the temporal predicate through the same SQL full-text
search uses. A built-in index receives it as an admission built from the FTS backend's
row filter and ranks only admitted chunks (#1746); an external index's matches are
intersected afterwards with an FTS-mode pass that carries every structured filter.
Either way a filter is only honored if it is both *counted* as a requested filter and
*forwarded* to whatever applies it.

Missing either half fails silently -- semantic search would answer a valid-time question
with unfiltered results, including the undated sources the filter excludes. These tests
pin both halves at the seam rather than trusting the call sites to stay in step.
"""

from dataclasses import replace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from basic_memory.repository.search_scope import ProjectScope
from basic_memory.temporal import TemporalFilter, TimeKind, parse_point
from tests.repository.test_hybrid_fusion import (
    OfflineSemanticSearch,
    HYBRID_QUERY,
    FakeFts,
    FakeRow as HybridFakeRow,
    fake_vector_retrieval,
    window_of,
)
from tests.repository.test_vector_threshold import (
    VECTOR_QUERY,
    FakeRow,
    _make_vector_rows,
    run_vector_only,
    vector_semantic,
)

TEMPORAL = TemporalFilter(kind=TimeKind.EFFECTIVE, at=parse_point("2026-07-28"))


@pytest.mark.asyncio
async def test_temporal_filter_reaches_a_built_in_index_as_its_admission():
    """A built-in index is handed the valid-time filter, compiled by the FTS backend."""
    filter_pass = FakeFts([])
    semantic = vector_semantic(fts=filter_pass)
    nearest = AsyncMock(return_value=_make_vector_rows([0.9]))

    with (
        patch.object(semantic, "_run_vector_query", nearest),
        patch.object(
            semantic,
            "_fetch_search_index_rows_by_ids",
            AsyncMock(return_value={("entity", 0): FakeRow(id=0)}),
        ),
    ):
        results = await semantic.vector_only(
            replace(VECTOR_QUERY, temporal=TEMPORAL), limit=10, offset=0
        )

    assert [row.id for row in results] == [0]
    # Forwarded unchanged to the row filter, without the search text...
    [row_query] = filter_pass.queries
    assert row_query.temporal is TEMPORAL
    assert row_query.search_text is None
    # ...and handed to the index as the admission, with no filter pass afterwards.
    assert nearest.await_args is not None
    admission = nearest.await_args.kwargs["admission"]
    assert admission is not None
    assert "(c.project_id, c.source_type, c.source_row_id) IN (" in admission.predicate
    assert filter_pass.calls == []


@pytest.mark.asyncio
async def test_temporal_filter_applies_in_vector_mode():
    """An external index's candidates are narrowed by the filter pass, forwarded verbatim."""
    # The embedding neighbourhood offers three entities; only entity 1 asserts a range
    # covering the queried date, so the FTS intersection pass returns just that one.
    filter_pass = FakeFts([FakeRow(id=1)])
    semantic = vector_semantic(fts=filter_pass, index_name="milvus")

    results = await run_vector_only(
        semantic,
        _make_vector_rows([0.9, 0.8, 0.7]),
        AsyncMock(return_value={("entity", i): FakeRow(id=i) for i in range(3)}),
        query=replace(VECTOR_QUERY, temporal=TEMPORAL),
    )

    assert [row.id for row in results] == [1]
    # Counted as a requested filter...
    assert len(filter_pass.queries) == 1
    # ...and forwarded unchanged, so the intersection asks the same question,
    # about the candidates themselves rather than a page of the whole match set.
    assert filter_pass.queries[0].temporal is TEMPORAL
    assert filter_pass.queries[0].search_text is None
    assert filter_pass.calls[0]["candidate_keys"] == [("entity", 0), ("entity", 1), ("entity", 2)]


@pytest.mark.asyncio
async def test_vector_mode_without_a_temporal_filter_runs_no_intersection_pass():
    """An unfiltered semantic search must not pay for a filter pass it does not need."""
    filter_pass = FakeFts([])
    semantic = vector_semantic(fts=filter_pass)

    results = await run_vector_only(
        semantic,
        _make_vector_rows([0.9]),
        AsyncMock(return_value={("entity", 0): FakeRow(id=0)}),
    )

    assert [row.id for row in results] == [0]
    assert filter_pass.queries == []


@pytest.mark.asyncio
async def test_temporal_filter_applies_in_hybrid_mode():
    """Hybrid fuses two legs; both must ask the same valid-time question."""
    fts_leg = FakeFts([HybridFakeRow(id=1, score=5.0, title="dated")])
    semantic = OfflineSemanticSearch(
        cast(Any, None), ProjectScope.single(1), fts_leg, fake_vector_retrieval()
    )
    vector_leg = AsyncMock(return_value=window_of([HybridFakeRow(id=1, score=0.9, title="dated")]))

    with patch.object(semantic, "_vector_window", vector_leg):
        results = await semantic.hybrid(
            replace(HYBRID_QUERY, temporal=TEMPORAL), limit=10, offset=0
        )

    assert [row.id for row in results] == [1]
    assert fts_leg.queries[0].temporal is TEMPORAL
    assert vector_leg.await_args is not None, "vector leg was never awaited"
    assert vector_leg.await_args.args[0].temporal is TEMPORAL
