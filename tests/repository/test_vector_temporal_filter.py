"""Valid-time filters reach the semantic retrieval modes too (SPEC-82).

Vector and hybrid search do not evaluate the temporal predicate themselves: they build a
candidate set from embeddings and then intersect it with an FTS-mode pass that carries
every structured filter. That means a filter is only honored in those modes if it is
both *counted* as a requested filter and *forwarded* to the intersecting search.

Missing either half fails silently -- semantic search would answer a valid-time question
with unfiltered results, including the undated sources the filter excludes. These tests
pin both halves at the seam rather than trusting the call sites to stay in step.
"""

from dataclasses import replace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from basic_memory.repository.search_reader import SemanticSearch
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.temporal import TemporalFilter, TimeKind, parse_point
from tests.repository.test_hybrid_fusion import (
    HYBRID_QUERY,
    FakeFts,
    FakeRow as HybridFakeRow,
    fake_vector_retrieval,
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
async def test_temporal_filter_applies_in_vector_mode():
    """A valid-time filter narrows the vector candidate set, and is forwarded verbatim."""
    # The embedding neighbourhood offers three entities; only entity 1 asserts a range
    # covering the queried date, so the FTS intersection pass returns just that one.
    filter_pass = FakeFts([FakeRow(id=1)])
    semantic = vector_semantic(fts=filter_pass)

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
    semantic = SemanticSearch(
        cast(Any, None), ProjectScope.single(1), fts_leg, fake_vector_retrieval()
    )
    vector_leg = AsyncMock(return_value=[HybridFakeRow(id=1, score=0.9, title="dated")])

    with patch.object(semantic, "vector_only", vector_leg):
        results = await semantic.hybrid(
            replace(HYBRID_QUERY, temporal=TEMPORAL), limit=10, offset=0
        )

    assert [row.id for row in results] == [1]
    assert fts_leg.queries[0].temporal is TEMPORAL
    assert vector_leg.await_args is not None, "vector leg was never awaited"
    assert vector_leg.await_args.args[0].temporal is TEMPORAL
