"""Valid-time filters reach the semantic retrieval modes too (SPEC-82).

Vector and hybrid search do not evaluate the temporal predicate themselves: they build a
candidate set from embeddings and then intersect it with an FTS-mode pass that carries
every structured filter. That means a filter is only honored in those modes if it is
both *counted* as a requested filter and *forwarded* to the intersecting search.

Missing either half fails silently -- semantic search would answer a valid-time question
with unfiltered results, including the undated sources the filter excludes. These tests
pin both halves at the seam rather than trusting the call sites to stay in step.
"""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from basic_memory.temporal import TemporalFilter, TimeRole, parse_point
from tests.repository.test_hybrid_fusion import (
    HYBRID_KWARGS,
    ConcreteSearchRepo as HybridSearchRepo,
    FakeRow as HybridFakeRow,
)
from tests.repository.test_vector_threshold import (
    COMMON_SEARCH_KWARGS,
    ConcreteSearchRepo as VectorSearchRepo,
    FakeRow,
    _fake_embedding_provider,
    _make_vector_rows,
    fake_scoped_session,
)

TEMPORAL = TemporalFilter(role=TimeRole.EFFECTIVE, at=parse_point("2026-07-28"))


def _vector_kwargs(**overrides: Any) -> dict[str, Any]:
    return {**COMMON_SEARCH_KWARGS, **overrides}


def _hybrid_kwargs(**overrides: Any) -> dict[str, Any]:
    return {**HYBRID_KWARGS, **overrides}


def _forwarded_temporal(leg: AsyncMock) -> Any:
    """The `temporal` argument one retrieval leg was actually called with."""
    assert leg.await_args is not None, "leg was never awaited"
    return leg.await_args.kwargs["temporal"]


@pytest.mark.asyncio
async def test_temporal_filter_applies_in_vector_mode():
    """A valid-time filter narrows the vector candidate set, and is forwarded verbatim."""
    repo = VectorSearchRepo()
    repo._semantic_min_similarity = 0.0
    repo._embedding_provider = _fake_embedding_provider(AsyncMock(return_value=[0.0] * 384))

    # The embedding neighbourhood offers three entities; only entity 1 asserts a range
    # covering the queried date, so the FTS intersection pass returns just that one.
    filter_pass = AsyncMock(return_value=[FakeRow(id=1)])

    with (
        patch(
            "basic_memory.repository.search_repository_base.db.scoped_session", fake_scoped_session
        ),
        patch.object(repo, "_ensure_vector_tables", new_callable=AsyncMock),
        patch.object(repo, "_prepare_vector_session", new_callable=AsyncMock),
        patch.object(
            repo,
            "_run_vector_query",
            new_callable=AsyncMock,
            return_value=_make_vector_rows([0.9, 0.8, 0.7]),
        ),
        patch.object(
            repo,
            "_fetch_search_index_rows_by_ids",
            new_callable=AsyncMock,
            return_value={("entity", i): FakeRow(id=i) for i in range(3)},
        ),
        patch.object(repo, "search", filter_pass),
    ):
        results = await repo._search_vector_only(**_vector_kwargs(temporal=TEMPORAL))

    assert [row.id for row in results] == [1]
    # Counted as a requested filter...
    filter_pass.assert_awaited_once()
    # ...and forwarded unchanged, so the intersection asks the same question.
    assert _forwarded_temporal(filter_pass) is TEMPORAL


@pytest.mark.asyncio
async def test_vector_mode_without_a_temporal_filter_runs_no_intersection_pass():
    """An unfiltered semantic search must not pay for a filter pass it does not need."""
    repo = VectorSearchRepo()
    repo._semantic_min_similarity = 0.0
    repo._embedding_provider = _fake_embedding_provider(AsyncMock(return_value=[0.0] * 384))
    filter_pass = AsyncMock(return_value=[])

    with (
        patch(
            "basic_memory.repository.search_repository_base.db.scoped_session", fake_scoped_session
        ),
        patch.object(repo, "_ensure_vector_tables", new_callable=AsyncMock),
        patch.object(repo, "_prepare_vector_session", new_callable=AsyncMock),
        patch.object(
            repo,
            "_run_vector_query",
            new_callable=AsyncMock,
            return_value=_make_vector_rows([0.9]),
        ),
        patch.object(
            repo,
            "_fetch_search_index_rows_by_ids",
            new_callable=AsyncMock,
            return_value={("entity", 0): FakeRow(id=0)},
        ),
        patch.object(repo, "search", filter_pass),
    ):
        results = await repo._search_vector_only(**_vector_kwargs())

    assert [row.id for row in results] == [0]
    filter_pass.assert_not_awaited()


@pytest.mark.asyncio
async def test_temporal_filter_applies_in_hybrid_mode():
    """Hybrid fuses two legs; both must ask the same valid-time question."""
    repo = HybridSearchRepo()
    fts_leg = AsyncMock(return_value=[HybridFakeRow(id=1, score=5.0, title="dated")])
    vector_leg = AsyncMock(return_value=[HybridFakeRow(id=1, score=0.9, title="dated")])

    with (
        patch.object(repo, "search", fts_leg),
        patch.object(repo, "_search_vector_only", vector_leg),
    ):
        results = await repo._search_hybrid(**_hybrid_kwargs(temporal=TEMPORAL))

    assert [row.id for row in results] == [1]
    assert _forwarded_temporal(fts_leg) is TEMPORAL
    assert _forwarded_temporal(vector_leg) is TEMPORAL
