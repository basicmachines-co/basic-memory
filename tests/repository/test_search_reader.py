"""SearchReader dispatch, and the SemanticSearch edges the pipeline tests do not reach."""

from dataclasses import replace
from itertools import count
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from logfire.testing import CaptureLogfire, capfire as capfire

from basic_memory.repository.search_query import PreparedSearchQuery
from basic_memory.repository.search_reader import (
    Reranking,
    SearchReader,
    SemanticSearch,
    vector_eligible,
)
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.search_trace import ManifestReadiness, SearchTraceCollector
from basic_memory.repository.semantic_errors import SemanticSearchDisabledError
from basic_memory.repository.semantic_vector_index import (
    VectorKey,
    VectorRowMatch,
    VectorRowSearch,
)
from basic_memory.schemas.search import SearchRetrievalMode
from tests.repository.test_hybrid_fusion import (
    OfflineSemanticSearch,
    HYBRID_QUERY,
    FakeFts,
    FakeRow,
    fake_vector_retrieval,
    vector_leg,
)

SCOPE = ProjectScope.single(1)
SEMANTIC_MODES = [SearchRetrievalMode.VECTOR, SearchRetrievalMode.HYBRID]


def _semantic(fts: FakeFts | None = None) -> SemanticSearch:
    return OfflineSemanticSearch(cast(Any, None), SCOPE, fts or FakeFts(), fake_vector_retrieval())


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


# --- SemanticSearch edges ---


@pytest.mark.asyncio
async def test_empty_candidate_window_skips_the_adapter():
    adapter = MagicMock()
    adapter.search = AsyncMock(side_effect=AssertionError("adapter must not be consulted"))
    vector = replace(fake_vector_retrieval(), index=adapter)
    semantic = OfflineSemanticSearch(cast(Any, None), SCOPE, FakeFts(), vector)

    assert await semantic._run_vector_query(AsyncMock(), [0.1], 0) == []


@pytest.mark.asyncio
async def test_no_row_ids_means_no_row_fetch():
    session_maker = MagicMock(side_effect=AssertionError("no session for an empty fetch"))
    semantic = OfflineSemanticSearch(session_maker, SCOPE, FakeFts(), fake_vector_retrieval())

    assert await semantic._fetch_search_index_rows_by_ids([]) == {}


@pytest.mark.asyncio
async def test_hybrid_skips_rows_without_an_id_on_the_lexical_leg():
    fts = FakeFts([FakeRow(id=None, score=2.0), FakeRow(id=1, score=5.0)])
    semantic = _semantic(fts)
    vector_results = [FakeRow(id=2, score=0.8)]

    with patch.object(semantic, "_vector_window", vector_leg(vector_results)):
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

    with patch.object(semantic, "_vector_window", vector_leg([])):
        await semantic.hybrid(HYBRID_QUERY, limit=10, offset=0)

    warning.assert_called_once()
    assert warning.call_args.args[0].startswith("[SEMANTIC_SLOW_QUERY]")
    assert warning.call_args.kwargs["retrieval_mode"] == "hybrid"
    assert warning.call_args.kwargs["scope"] == (1,)


def row_match(row_id: int, similarity: float = 0.75) -> VectorRowMatch:
    """Row ``row_id``'s best chunk."""
    return VectorRowMatch(
        key=VectorKey(entity_id=row_id, chunk_key=f"entity:{row_id}:0"),
        similarity=similarity,
        project_id=1,
        source_type="entity",
        source_row_id=row_id,
    )


class RowIndex:
    """A built-in index over ``rows`` (nearest first), each cut into ``chunks_per_row``.

    A scan of ``chunk_scan`` chunks reaches the rows whose chunks it read, and is
    truncated while chunks remain past it. Records how it was asked.
    """

    max_chunk_scan = 4096

    def __init__(self, rows: list[VectorRowMatch], *, chunks_per_row: int = 1) -> None:
        self.rows = rows
        self.chunks_per_row = chunks_per_row
        self.calls: list[dict[str, Any]] = []

    async def search_rows(self, query, *, limit, chunk_scan, **kwargs) -> VectorRowSearch:
        self.calls.append({"query": query, "limit": limit, "chunk_scan": chunk_scan, **kwargs})
        reached = self.rows[: chunk_scan // self.chunks_per_row]
        return VectorRowSearch(
            matches=reached[:limit],
            truncated=chunk_scan < len(self.rows) * self.chunks_per_row,
        )


def _row_reader(index: RowIndex) -> SemanticSearch:
    return OfflineSemanticSearch(
        cast(Any, None), SCOPE, FakeFts(), replace(fake_vector_retrieval(), index=index)
    )


@pytest.mark.asyncio
async def test_a_truncated_row_scan_widens_until_the_page_fills():
    """Notes of hundreds of chunks can fill a chunk scan with too few rows (#1746).

    Five notes of 300 chunks each: a 100-row window scans 1,000 chunks and reaches three
    notes, so the scan doubles until it reaches the five the page needs.
    """
    index = RowIndex([row_match(row_id) for row_id in range(1, 6)], chunks_per_row=300)

    chunks = await _row_reader(index)._run_vector_query(AsyncMock(), [0.1], 100, row_target=5)

    assert [chunk.source_key[1] for chunk in chunks] == [1, 2, 3, 4, 5]
    assert [call["chunk_scan"] for call in index.calls] == [1000, 2000]
    assert all(call["limit"] == 100 for call in index.calls)


@pytest.mark.asyncio
async def test_the_row_scan_reports_on_the_vector_query_span(capfire: CaptureLogfire):
    """Rounds and scan size ride on the one search.vector_query span, not a record of their own.

    Logfire bills per record and every vector search runs this once.
    """
    index = RowIndex([row_match(row_id) for row_id in range(1, 6)], chunks_per_row=300)
    capfire.exporter.clear()

    await _row_reader(index)._run_vector_query(AsyncMock(), [0.1], 100, row_target=5)

    spans = capfire.exporter.exported_spans_as_dict()
    [query_span] = [span for span in spans if span["name"] == "search.vector_query"]
    attributes = query_span["attributes"]
    assert (
        attributes["rounds"],
        attributes["chunk_scan"],
        attributes["rows"],
        attributes["row_target"],
        attributes["truncated"],
    ) == (2, 2000, 5, 5, False)
    assert [span["name"] for span in spans] == ["search.vector_query"]


@pytest.mark.asyncio
async def test_a_row_scan_stops_at_the_index_limit_or_when_it_saw_every_chunk():
    """A short window is the answer once the scan saw everything or cannot grow."""
    # Every chunk was read: three rows exist, so the page stays short.
    complete = RowIndex([row_match(row_id) for row_id in range(1, 4)])
    found = await _row_reader(complete)._run_vector_query(AsyncMock(), [0.1], 20, row_target=5)
    assert len(found) == 3
    assert len(complete.calls) == 1

    # Truncated, but the scan already reads the most chunks the index allows.
    capped = RowIndex([row_match(row_id) for row_id in range(1, 10)], chunks_per_row=2000)
    found = await _row_reader(capped)._run_vector_query(AsyncMock(), [0.1], 500, row_target=5)
    assert len(found) == 2
    assert [call["chunk_scan"] for call in capped.calls] == [4096]


@pytest.mark.asyncio
async def test_built_in_adapter_reads_manifest_readiness_when_tracing(monkeypatch):
    """A traced built-in lookup reports how much of the manifest retrieval can answer from."""

    adapter = RowIndex([row_match(80)])
    semantic = OfflineSemanticSearch(
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

    [chunk] = await semantic._run_vector_query(session, [0.1], 5, trace=trace)

    # The row's best chunk comes back named with the search row it was cut from.
    assert (chunk.source_key, chunk.chunk_key, chunk.similarity) == (
        ("entity", 80),
        "entity:80:0",
        0.75,
    )
    assert trace.vector is not None
    assert [(m.key, m.chunk_key) for m in trace.vector.chunk_matches] == [
        (("entity", 80), "entity:80:0")
    ]
    assert trace.vector.drops == ()
    assert trace.readiness is readiness
    read_readiness.assert_awaited_once_with(session, SCOPE, "sqlite-vec", "fake:384")
    # Five rows, read through a scan of ten chunks per row.
    assert adapter.calls == [
        {"query": [0.1], "limit": 5, "chunk_scan": 50, "projects": SCOPE, "admission": None}
    ]


@pytest.mark.asyncio
async def test_fts_gate_zeroes_weak_lexical_scores(monkeypatch):
    """Below the gate a lexical hit contributes nothing to fusion instead of a sliver."""
    monkeypatch.setattr("basic_memory.repository.search_reader.FTS_GATE_THRESHOLD", 0.5)
    semantic = _semantic(FakeFts([FakeRow(id=1, score=10.0), FakeRow(id=2, score=1.0)]))

    with patch.object(semantic, "_vector_window", vector_leg([])):
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
async def test_hybrid_page_past_the_prefix_retrieves_once():
    """A page past the fixed rerank prefix reads that prefix from its own retrieval (#1557)."""
    rows = [
        FakeRow(id=index, score=10.0 - index, title=f"n{index}", entity_id=index)
        for index in range(1, 6)
    ]
    fts = FakeFts(rows)
    semantic = OfflineSemanticSearch(
        cast(Any, None),
        SCOPE,
        fts,
        replace(fake_vector_retrieval(), vector_k=2),
        Reranking(provider=_PrefixReranker(), candidates=2, max_document_chars=0),
    )
    vector_window = vector_leg([])

    with patch.object(semantic, "_vector_window", vector_window):
        page = await semantic.hybrid(HYBRID_QUERY, limit=1, offset=3)

    assert [row.id for row in page] == [4]
    assert len(fts.calls) == 1
    vector_window.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_scope_answers_nothing_without_embedding_or_lexical_work() -> None:
    """An empty scope admits no rows, so neither leg runs and the query is never embedded."""
    fts = FakeFts([FakeRow(id=1)])
    embed_query = AsyncMock(side_effect=AssertionError("must not embed for an empty scope"))
    semantic = OfflineSemanticSearch(
        cast(Any, None), ProjectScope.of([]), fts, fake_vector_retrieval(embed_query=embed_query)
    )
    vector_query = PreparedSearchQuery(
        search_text="auth", retrieval_mode=SearchRetrievalMode.VECTOR
    )

    assert await semantic.vector_only(vector_query, limit=10, offset=0) == []
    assert await semantic.hybrid(HYBRID_QUERY, limit=10, offset=0) == []
    assert fts.queries == []
