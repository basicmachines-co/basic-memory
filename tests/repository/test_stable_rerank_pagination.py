"""One retrieval per request keeps reranked pagination stable (#1557).

A page whose window reaches past the fixed reranked prefix retrieves a larger window,
and the prefix must still be the one every shallower page reranked. These tests page
through real SQLite and Postgres retrieval and count the retrieval spans per request.
"""

import math
import zlib
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any, override

import pytest
from logfire.testing import CaptureLogfire, capfire as capfire

from basic_memory.config import BasicMemoryConfig
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_query import PreparedSearchQuery
from basic_memory.repository.search_reader import HydratedChunk, SearchReader, SemanticSearch
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.schemas.search import SearchRetrievalMode
from tests.repository.test_rerank_pipeline import (
    BackendSearchRepository,
    _entity_row,
    _FakeReranker,
    _semantic_search_repository,
    _StubEmbeddingProvider,
)

type Page = Callable[[int, int], Awaitable[list[SearchIndexRow]]]

QUERY_TEXT = "auth session token"


class _DistinctEmbeddingProvider(_StubEmbeddingProvider):
    """The stub's vectors, each tilted by its text so no two texts tie exactly.

    sqlite-vec picks arbitrarily among equal distances at its ``k`` cutoff, so windows
    of different sizes can disagree on which of several identical vectors they hold.
    That affects every sqlite-vec window boundary, not only the reranked prefix, so
    the corpus gives each passage its own distance.
    """

    @staticmethod
    @override
    def _vectorize(text: str) -> list[float]:
        x, y, z, w = _StubEmbeddingProvider._vectorize(text)
        z += zlib.crc32(text.encode()) % 997 / 10_000
        norm = math.sqrt(x * x + y * y + z * z + w * w)
        return [x / norm, y / norm, z / norm, w / norm]


@pytest.fixture
async def pagination_repository(
    session_maker: Any,
    test_project: Any,
    app_config: BasicMemoryConfig,
) -> BackendSearchRepository:
    """32 notes whose rows straddle a two-row reranked prefix read from eight chunks.

    Two long notes split into several chunks, one note has only vector evidence, one
    only lexical evidence (never embedded), and one sits outside the filtered folder.
    """
    repo = _semantic_search_repository(
        session_maker, test_project.id, app_config, _DistinctEmbeddingProvider()
    )
    repo._semantic_vector_k = 8
    repo._reranker_candidates = 2
    repo._reranker_max_document_chars = 2000
    await repo.init_search_index()
    for index in range(32):
        content = f"auth session token note{index} " + ("deep " if index % 2 else "")
        if index < 2:
            content = "\n\n".join(f"{content}passage {n} " + "detail " * 160 for n in range(3))
        if index == 30:
            content = "oauth related concepts without lexical terms"
        row = _entity_row(
            project_id=repo.project_id,
            row_id=700 + index,
            title=f"Note {index:02d}",
            permalink=f"{'excluded' if index == 31 else 'notes'}/{index:02d}",
            content=content,
        )
        await repo.index_item(row)
        if index != 29:
            await repo.sync_entity_vectors(row.id)
    return repo


def _project_route(repo: BackendSearchRepository, mode: SearchRetrievalMode) -> Page:
    async def page(limit: int, offset: int) -> list[SearchIndexRow]:
        return await repo.search(
            search_text=QUERY_TEXT,
            retrieval_mode=mode,
            file_path_prefix="notes",
            min_similarity=0.5,
            limit=limit,
            offset=offset,
        )

    return page


def _scoped_route(repo: BackendSearchRepository, mode: SearchRetrievalMode) -> Page:
    """The reader a scoped ``QUERY /v2/search/`` builds, over an explicit project scope."""

    async def page(limit: int, offset: int) -> list[SearchIndexRow]:
        scope = ProjectScope.of([repo.project_id])
        project_semantic = repo._semantic_search()
        semantic = SemanticSearch(
            repo.session_maker,
            scope,
            repo._fts,
            project_semantic.vector,
            project_semantic.rerank,
        )
        query = PreparedSearchQuery(
            search_text=QUERY_TEXT,
            retrieval_mode=mode,
            file_path_prefix="notes",
            min_similarity=0.5,
        )
        return await SearchReader(scope, repo._fts, semantic).search(
            query, limit=limit, offset=offset
        )

    return page


@pytest.mark.asyncio
@pytest.mark.parametrize("route", [_project_route, _scoped_route])
@pytest.mark.parametrize("mode", [SearchRetrievalMode.VECTOR, SearchRetrievalMode.HYBRID])
@pytest.mark.parametrize("enabled", [False, True])
async def test_one_retrieval_per_page_and_stable_reranked_pages(
    pagination_repository: BackendSearchRepository,
    route: Callable[[BackendSearchRepository, SearchRetrievalMode], Page],
    mode: SearchRetrievalMode,
    enabled: bool,
    capfire: CaptureLogfire,
) -> None:
    repo = pagination_repository
    reranker = _FakeReranker({f"Note {i:02d}": (i + 1) / 33 for i in range(32)})
    repo._rerank_provider = reranker if enabled else None
    if not enabled:
        # The disabled path keeps its best-effort chunk window. Keep this corpus
        # within it while checking unchanged ranking across page sizes.
        repo._semantic_vector_k = 100
    await repo._ensure_vector_tables()
    search_page = route(repo, mode)

    async def page(limit: int, offset: int = 0) -> list[SearchIndexRow]:
        capfire.exporter.clear()
        calls_before = reranker.calls
        rows = await search_page(limit, offset)
        spans = Counter(span["name"] for span in capfire.exporter.exported_spans_as_dict())
        # One retrieval pass: the query is embedded once, lexical retrieval runs once
        # for hybrid, and the reranker scores the fixed prefix once (#1557).
        assert spans["search.embed_query"] == 1
        assert spans["search.fts"] == int(mode == SearchRetrievalMode.HYBRID)
        assert reranker.calls - calls_before == int(enabled and bool(rows))
        assert all(row.file_path.startswith("notes/") for row in rows)
        return rows

    complete = await page(40)
    assert len(complete) == (31 if mode == SearchRetrievalMode.HYBRID else 30)
    expected = [(row.type, row.id) for row in complete]
    for size in (1, 4, 13):
        paged = [
            row for offset in range(0, len(complete), size) for row in await page(size, offset)
        ]
        assert [(row.type, row.id) for row in paged] == expected
        assert [row.score for row in paged] == [row.score for row in complete]
    assert await page(4, 100) == []
    if enabled:
        # Every page reranked the same prefix with the same documents.
        assert reranker.document_batches
        assert all(batch == reranker.document_batches[0] for batch in reranker.document_batches)
        assert all(len(doc) <= 2000 for doc in reranker.document_batches[0])


def _stub_vector_stream(monkeypatch: pytest.MonkeyPatch, stream: list[HydratedChunk]) -> list[int]:
    """Serve ``stream`` as the adapter ranking, sliced to each window, recording each size."""
    windows: list[int] = []

    async def run_vector_query(
        self: SemanticSearch,
        session: Any,
        query_embedding: list[float],
        candidate_limit: int,
        *,
        trace: Any = None,
    ) -> list[HydratedChunk]:
        windows.append(candidate_limit)
        return stream[:candidate_limit]

    monkeypatch.setattr(SemanticSearch, "_run_vector_query", run_vector_query)
    return windows


@pytest.mark.asyncio
async def test_hybrid_tail_orders_by_chunk_position_before_collapse(
    pagination_repository: BackendSearchRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A late vector-only row cannot jump ahead of lexical tail rows already returned.

    One note's hundred chunks crowd the vector ranking, so the second vector row is
    only its second row but enters the ranking at chunk 100.
    """
    repo = pagination_repository
    repo._rerank_provider = _FakeReranker({"Note": 0.8})
    stream = [
        HydratedChunk(
            entity_id=700,
            chunk_key=f"entity:700:{i}",
            chunk_text=f"auth {i}",
            similarity=1 - i / 1000,
        )
        for i in range(100)
    ] + [HydratedChunk(entity_id=730, chunk_key="entity:730:0", chunk_text="oauth", similarity=0.8)]
    windows = _stub_vector_stream(monkeypatch, stream)

    async def search(limit: int, offset: int = 0) -> list[SearchIndexRow]:
        return await repo.search(
            search_text=QUERY_TEXT,
            retrieval_mode=SearchRetrievalMode.HYBRID,
            limit=limit,
            offset=offset,
        )

    complete = await search(40)
    windows.clear()
    paged = [row for offset in range(len(complete)) for row in await search(1, offset)]

    assert len(complete) == 32
    assert [row.id for row in paged] == [row.id for row in complete]
    assert paged[-1].id == 730
    # Each page read one vector window.
    assert len(windows) == len(complete)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [SearchRetrievalMode.VECTOR, SearchRetrievalMode.HYBRID])
async def test_fixed_prefix_keeps_the_passages_the_fixed_window_read(
    pagination_repository: BackendSearchRepository,
    monkeypatch: pytest.MonkeyPatch,
    mode: SearchRetrievalMode,
) -> None:
    """A deeper window's extra chunks of a prefix row never reach the reranker."""
    repo = pagination_repository
    # A long note quotes its matched passages to the reranker instead of its content.
    await repo.index_item(
        _entity_row(
            project_id=repo.project_id,
            row_id=730,
            title="Original passage",
            permalink="notes/30",
            content="body " * 1000,
        )
    )
    stream = [
        HydratedChunk(
            entity_id=700,
            chunk_key=f"entity:700:{i}",
            chunk_text=f"first {i}",
            similarity=1 - i / 1000,
        )
        for i in range(7)
    ] + [
        HydratedChunk(
            entity_id=730, chunk_key="entity:730:0", chunk_text="ORIGINAL PASSAGE", similarity=0.9
        ),
        HydratedChunk(
            entity_id=730, chunk_key="entity:730:1", chunk_text="LATER PASSAGE", similarity=0.8
        ),
    ]
    _stub_vector_stream(monkeypatch, stream)
    reranker = _FakeReranker({"Original passage": 0.9, "Note 00": 0.8})
    repo._rerank_provider = reranker

    # A query no note matches lexically, so the hybrid prefix is its vector rows.
    for size in (2, 20):
        await repo.search(search_text="unmatchedquery", retrieval_mode=mode, limit=size)

    assert reranker.document_batches[0] == reranker.document_batches[1]
    assert any("ORIGINAL PASSAGE" in doc for doc in reranker.document_batches[0])
    assert all("LATER PASSAGE" not in doc for batch in reranker.document_batches for doc in batch)
