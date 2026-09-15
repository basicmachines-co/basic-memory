"""SearchReader dispatch, and the SemanticSearch edges the pipeline tests do not reach."""

from dataclasses import replace
from itertools import count
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from basic_memory.repository.search_query import PreparedSearchQuery
from basic_memory.repository.search_reader import (
    HydratedChunk,
    Reranking,
    SearchReader,
    SemanticSearch,
    parse_chunk_key,
    vector_eligible,
)
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.search_trace import ManifestReadiness, SearchTraceCollector
from basic_memory.repository.semantic_errors import SemanticSearchDisabledError
from basic_memory.schemas.search import SearchRetrievalMode
from tests.repository.test_hybrid_fusion import (
    HYBRID_QUERY,
    FakeFts,
    FakeRow,
    fake_vector_retrieval,
)
from tests.repository.test_vector_threshold import run_vector_only, vector_semantic

SCOPE = ProjectScope.single(1)
SEMANTIC_MODES = [SearchRetrievalMode.VECTOR, SearchRetrievalMode.HYBRID]


def _semantic(fts: FakeFts | None = None) -> SemanticSearch:
    return SemanticSearch(cast(Any, None), SCOPE, fts or FakeFts(), fake_vector_retrieval())


# --- Eligibility and keys ---


@pytest.mark.parametrize(
    ("query", "eligible"),
    [
        (PreparedSearchQuery(search_text="auth"), True),
        (PreparedSearchQuery(search_text="  auth  "), True),
        (PreparedSearchQuery(search_text=None), False),
        (PreparedSearchQuery(search_text="   "), False),
        (PreparedSearchQuery(search_text="*"), False),
        (PreparedSearchQuery(search_text="auth", permalink="specs/auth"), False),
        (PreparedSearchQuery(search_text="auth", permalink_match="specs/*"), False),
        (PreparedSearchQuery(search_text="auth", title="Auth"), False),
    ],
)
def test_vector_eligible_requires_text_and_no_identity_filter(query, eligible):
    assert vector_eligible(query) is eligible


def test_parse_chunk_key_reads_type_and_row_id():
    assert parse_chunk_key("observation:5:0") == ("observation", 5)
    with pytest.raises(ValueError):
        parse_chunk_key("entity:not-a-number:0")
    with pytest.raises(IndexError):
        parse_chunk_key("garbage")


# --- SearchReader dispatch ---


@pytest.mark.asyncio
async def test_fts_mode_hands_the_engine_the_query_and_every_option():
    fts = FakeFts([FakeRow(id=1)])
    reader = SearchReader(SCOPE, fts)
    query = PreparedSearchQuery(search_text="auth")

    rows = await reader.search(
        query,
        limit=5,
        offset=2,
        allow_relaxed=True,
        candidate_keys=[("entity", 1)],
    )

    assert [row.id for row in rows] == [1]
    assert fts.queries == [query]
    assert fts.calls == [
        {"limit": 5, "offset": 2, "allow_relaxed": True, "candidate_keys": [("entity", 1)]}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", SEMANTIC_MODES)
async def test_semantic_modes_reject_queries_with_nothing_to_embed(mode):
    reader = SearchReader(SCOPE, FakeFts(), _semantic())

    with pytest.raises(ValueError, match="requires a non-empty text query"):
        await reader.search(
            PreparedSearchQuery(title="Auth", retrieval_mode=mode), limit=10, offset=0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", SEMANTIC_MODES)
async def test_semantic_modes_without_a_semantic_stack_raise_disabled(mode):
    """A reader built without the semantic stack answers full-text queries only."""
    reader = SearchReader(SCOPE, FakeFts())

    with pytest.raises(SemanticSearchDisabledError):
        await reader.search(
            PreparedSearchQuery(search_text="auth", retrieval_mode=mode), limit=10, offset=0
        )


@pytest.mark.asyncio
async def test_semantic_modes_route_to_the_pipeline():
    semantic = _semantic()
    reader = SearchReader(SCOPE, FakeFts(), semantic)
    vector_query = PreparedSearchQuery(
        search_text="auth", retrieval_mode=SearchRetrievalMode.VECTOR
    )

    with patch.object(
        semantic, "vector_only", new_callable=AsyncMock, return_value=[FakeRow(id=1)]
    ) as vector_only:
        rows = await reader.search(vector_query, limit=3, offset=1)
    assert [row.id for row in rows] == [1]
    vector_only.assert_awaited_once_with(vector_query, limit=3, offset=1, trace=None)

    with patch.object(
        semantic, "hybrid", new_callable=AsyncMock, return_value=[FakeRow(id=2)]
    ) as hybrid:
        rows = await reader.search(HYBRID_QUERY, limit=3, offset=1)
    assert [row.id for row in rows] == [2]
    hybrid.assert_awaited_once_with(HYBRID_QUERY, limit=3, offset=1, trace=None)


@pytest.mark.asyncio
async def test_count_is_full_text_only():
    reader = SearchReader(SCOPE, FakeFts([FakeRow(id=1), FakeRow(id=2)]))

    assert await reader.count(PreparedSearchQuery(search_text="auth"), allow_relaxed=True) == 2
    with pytest.raises(ValueError, match="Exact counts are only supported"):
        await reader.count(
            PreparedSearchQuery(search_text="auth", retrieval_mode=SearchRetrievalMode.VECTOR)
        )


# --- SemanticSearch edges ---


@pytest.mark.asyncio
async def test_empty_candidate_window_skips_the_adapter():
    adapter = MagicMock()
    adapter.search = AsyncMock(side_effect=AssertionError("adapter must not be consulted"))
    vector = replace(fake_vector_retrieval(), index=adapter)
    semantic = SemanticSearch(cast(Any, None), SCOPE, FakeFts(), vector)

    assert await semantic._run_vector_query(AsyncMock(), [0.1], 0) == []


@pytest.mark.asyncio
async def test_no_row_ids_means_no_row_fetch():
    session_maker = MagicMock(side_effect=AssertionError("no session for an empty fetch"))
    semantic = SemanticSearch(session_maker, SCOPE, FakeFts(), fake_vector_retrieval())

    assert await semantic._fetch_search_index_rows_by_ids([]) == {}


@pytest.mark.asyncio
async def test_vector_only_with_no_parseable_chunk_keys_returns_nothing():
    semantic = vector_semantic()
    fetch_rows = AsyncMock()
    rows = [HydratedChunk(entity_id=0, chunk_key="garbage", chunk_text="bad", similarity=0.95)]

    assert await run_vector_only(semantic, rows, fetch_rows) == []
    fetch_rows.assert_not_called()


@pytest.mark.asyncio
async def test_hybrid_skips_rows_without_an_id_on_both_legs():
    fts = FakeFts([FakeRow(id=None, score=2.0), FakeRow(id=1, score=5.0)])
    semantic = _semantic(fts)
    vector_results = [FakeRow(id=None, score=0.9), FakeRow(id=2, score=0.8)]

    with patch.object(semantic, "vector_only", new_callable=AsyncMock, return_value=vector_results):
        rows = await semantic.hybrid(HYBRID_QUERY, limit=10, offset=0)

    assert [(row.type, row.id) for row in rows] == [("entity", 1), ("entity", 2)]


@pytest.mark.asyncio
async def test_hybrid_slow_query_warning_names_the_scope(monkeypatch):
    semantic = _semantic(FakeFts([FakeRow(id=1, score=5.0)]))
    clock = count(0.0, 3.0)
    monkeypatch.setattr(
        "basic_memory.repository.search_reader.time.perf_counter", lambda: next(clock)
    )
    warning = MagicMock()
    monkeypatch.setattr("basic_memory.repository.search_reader.logger.warning", warning)

    with patch.object(semantic, "vector_only", new_callable=AsyncMock, return_value=[]):
        await semantic.hybrid(HYBRID_QUERY, limit=10, offset=0)

    warning.assert_called_once()
    assert warning.call_args.args[0].startswith("[SEMANTIC_SLOW_QUERY]")
    assert warning.call_args.kwargs["retrieval_mode"] == "hybrid"
    assert warning.call_args.kwargs["scope"] == (1,)


@pytest.mark.asyncio
async def test_built_in_adapter_reads_manifest_readiness_when_tracing(monkeypatch):
    """A traced built-in lookup reports how much of the manifest retrieval can answer from."""
    adapter = MagicMock()
    adapter.search = AsyncMock(return_value=[])
    semantic = SemanticSearch(
        cast(Any, None), SCOPE, FakeFts(), replace(fake_vector_retrieval(), index=adapter)
    )
    readiness = ManifestReadiness(
        configured_index="sqlite-vec",
        configured_model="fake:384",
        ready_rows=3,
        pending_rows=1,
        other_identity_rows=0,
    )
    read_readiness = AsyncMock(return_value=readiness)
    monkeypatch.setattr(
        "basic_memory.repository.search_reader.read_manifest_readiness", read_readiness
    )
    trace = SearchTraceCollector()
    session = AsyncMock()

    assert await semantic._run_vector_query(session, [0.1], 5, trace=trace) == []

    assert trace.readiness is readiness
    read_readiness.assert_awaited_once_with(session, SCOPE, "sqlite-vec", "fake:384")
    adapter.search.assert_awaited_once_with([0.1], limit=5, projects=SCOPE)


@pytest.mark.asyncio
async def test_fts_gate_zeroes_weak_lexical_scores(monkeypatch):
    """Below the gate a lexical hit contributes nothing to fusion instead of a sliver."""
    monkeypatch.setattr("basic_memory.repository.search_reader.FTS_GATE_THRESHOLD", 0.5)
    semantic = _semantic(FakeFts([FakeRow(id=1, score=10.0), FakeRow(id=2, score=1.0)]))

    with patch.object(semantic, "vector_only", new_callable=AsyncMock, return_value=[]):
        rows = await semantic.hybrid(HYBRID_QUERY, limit=10, offset=0)

    assert [(row.id, row.score) for row in rows] == [(1, 1.0), (2, 0.0)]


class _PrefixReranker:
    """Scores documents by position so the rerank stage is deterministic."""

    model_name = "prefix"

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        return [1.0 - index * 0.1 for index in range(len(documents))]

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {}


@pytest.mark.asyncio
async def test_hybrid_trace_records_the_stable_pool_refetch():
    """A page past the fixed rerank prefix refetches the stable pool, and the trace says so."""
    rows = [
        FakeRow(id=index, score=10.0 - index, title=f"n{index}", entity_id=index)
        for index in range(1, 6)
    ]
    semantic = SemanticSearch(
        cast(Any, None),
        SCOPE,
        FakeFts(rows),
        replace(fake_vector_retrieval(), vector_k=2),
        Reranking(provider=_PrefixReranker(), candidates=2, max_document_chars=0),
    )
    trace = SearchTraceCollector()

    with patch.object(semantic, "vector_only", new_callable=AsyncMock, return_value=[]):
        page = await semantic.hybrid(HYBRID_QUERY, limit=1, offset=3, trace=trace)

    assert trace.stable_pool_refetched is True
    assert [row.id for row in page] == [4]
