"""Single-pass pagination through real SQLite/pgvector retrieval and public boundaries."""

from collections import Counter
from typing import Any

import pytest
from logfire.testing import CaptureLogfire, capfire as capfire
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory import db
from basic_memory.config import DatabaseBackend
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_trace import SearchTraceCollector
from basic_memory.schemas.search import SearchRetrievalMode
from semantic_search_helpers import (
    BackendSearchRepository,
    _FakeReranker,
    _entity_row,
    pagination_repository as pagination_repository,
    rerank_search_repository as rerank_search_repository,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [SearchRetrievalMode.VECTOR, SearchRetrievalMode.HYBRID])
@pytest.mark.parametrize("enabled", [False, True])
async def test_one_pass_stable_pages_and_fixed_reranker_documents(
    pagination_repository: BackendSearchRepository,
    mode: SearchRetrievalMode,
    enabled: bool,
    capfire: CaptureLogfire,
) -> None:
    repo = pagination_repository
    reranker = _FakeReranker({f"Note {i:02d}": (i + 1) / 33 for i in range(32)})
    repo._rerank_provider = reranker if enabled else None
    if not enabled:
        # The legacy disabled path has a best-effort chunk window. Keep this corpus
        # within it while testing unchanged ranking and one retrieval on later pages.
        repo._semantic_vector_k = 100

    async def page(limit: int, offset: int = 0) -> list[SearchIndexRow]:
        capfire.exporter.clear()
        collector = SearchTraceCollector()
        calls_before = reranker.calls
        rows = await repo.search(
            search_text="auth session token",
            retrieval_mode=mode,
            file_path_prefix="notes",
            min_similarity=0.5,
            limit=limit,
            offset=offset,
            trace=collector,
        )
        names = Counter(span["name"] for span in capfire.exporter.exported_spans_as_dict())
        assert names["search.embed_query"] == names["search.vector_query"] == 1
        assert names["search.fts"] == int(mode == SearchRetrievalMode.HYBRID)
        assert reranker.calls - calls_before == int(enabled and bool(rows))
        assert collector.stable_pool_refetched is False
        assert all(row.project_id == repo.project_id for row in rows)
        assert all(row.file_path.startswith("notes/") for row in rows)
        return rows

    complete = await page(40)
    assert len(complete) == (31 if mode == SearchRetrievalMode.HYBRID else 30)
    expected = [(row.type, row.id) for row in complete]
    for size in (1, 4, 13):
        actual = [
            row for offset in range(0, len(complete), size) for row in await page(size, offset)
        ]
        assert [(row.type, row.id) for row in actual] == expected
        assert len({(row.type, row.id) for row in actual}) == len(expected)
        assert [row.score for row in actual] == [row.score for row in complete]
    assert await page(4, 100) == []
    if enabled:
        assert reranker.document_batches
        assert all(batch == reranker.document_batches[0] for batch in reranker.document_batches)
        assert all(len(doc) <= 2000 for doc in reranker.document_batches[0])


@pytest.mark.asyncio
async def test_hybrid_tail_orders_by_chunk_admission_before_collapse(
    pagination_repository: BackendSearchRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A late vector-only row cannot jump ahead of previously returned FTS tail rows."""
    repo = pagination_repository
    repo._rerank_provider = _FakeReranker({"Note": 0.8})
    # Model a multi-chunk result crowding the raw window. The second unique row
    # appears only after 100 chunks, so its document rank (1) is not its admission rank.
    chunks = [
        {
            "chunk_key": f"entity:700:{i}",
            "chunk_text": f"auth passage {i}",
            "best_similarity": 1.0 - i / 1000,
        }
        for i in range(100)
    ] + [{"chunk_key": "entity:730:0", "chunk_text": "oauth", "best_similarity": 0.8}]

    async def vector_query(
        session: AsyncSession,
        embedding: list[float],
        candidate_limit: int,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        return chunks[:candidate_limit]

    monkeypatch.setattr(repo, "_run_vector_query", vector_query)
    complete = await repo.search(
        search_text="auth session token", retrieval_mode=SearchRetrievalMode.HYBRID, limit=40
    )
    assert len(complete) == 32
    pages = [
        row
        for offset in range(len(complete))
        for row in await repo.search(
            search_text="auth session token",
            retrieval_mode=SearchRetrievalMode.HYBRID,
            limit=1,
            offset=offset,
        )
    ]
    assert [row.id for row in pages] == [row.id for row in complete]
    assert pages[-1].id == 730


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [SearchRetrievalMode.VECTOR, SearchRetrievalMode.HYBRID])
async def test_fixed_prefix_keeps_its_original_matched_passages(
    pagination_repository: BackendSearchRepository,
    monkeypatch: pytest.MonkeyPatch,
    mode: SearchRetrievalMode,
) -> None:
    repo = pagination_repository
    # Use a vector-only query for the hybrid case too: empty FTS must not change
    # the fixed vector documents. A long note uses matched passages for reranking.
    row = _entity_row(
        project_id=repo.project_id,
        row_id=730,
        title="Original passage",
        permalink="notes/30",
        content="body " * 1000,
    )
    await repo.index_item(row)
    chunks = [
        {
            "chunk_key": f"entity:700:{i}",
            "chunk_text": f"first note {i}",
            "best_similarity": 1.0 - i / 1000,
        }
        for i in range(7)
    ] + [
        {"chunk_key": "entity:730:0", "chunk_text": "ORIGINAL PASSAGE", "best_similarity": 0.9},
        {"chunk_key": "entity:730:1", "chunk_text": "LATER PASSAGE", "best_similarity": 0.8},
    ]

    async def vector_query(
        session: AsyncSession,
        embedding: list[float],
        candidate_limit: int,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        return chunks[:candidate_limit]

    monkeypatch.setattr(repo, "_run_vector_query", vector_query)
    reranker = _FakeReranker({"Original passage": 0.9, "Note 00": 0.8})
    repo._rerank_provider = reranker
    for size in (2, 20):
        await repo.search(search_text="unmatchedquery", retrieval_mode=mode, limit=size)
    assert reranker.document_batches[0] == reranker.document_batches[1]
    assert any("ORIGINAL PASSAGE" in doc for doc in reranker.document_batches[0])
    assert all("LATER PASSAGE" not in doc for batch in reranker.document_batches for doc in batch)


@pytest.mark.asyncio
async def test_dropped_adapter_slots_do_not_refill_fixed_prefix(
    pagination_repository: BackendSearchRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = pagination_repository
    reranker = _FakeReranker({"Note 00": 0.8, "Note 30": 0.9})
    repo._rerank_provider = reranker

    async def vector_query(
        session: AsyncSession,
        embedding: list[float],
        candidate_limit: int,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        return [
            {
                "chunk_key": "entity:700:0",
                "chunk_text": "kept",
                "best_similarity": 1.0,
                "candidate_rank": 0,
            },
            {
                "chunk_key": "entity:730:0",
                "chunk_text": "later",
                "best_similarity": 0.9,
                "candidate_rank": 8,
            },
        ]

    monkeypatch.setattr(repo, "_run_vector_query", vector_query)
    rows = await repo.search(search_text="auth", retrieval_mode=SearchRetrievalMode.VECTOR, limit=3)
    assert [row.id for row in rows] == [700, 730]
    assert len(reranker.document_batches) == 1
    assert len(reranker.document_batches[0]) == 1
    assert "Note 00" in reranker.document_batches[0][0]


@pytest.mark.asyncio
async def test_prefix_threshold_uses_only_its_own_chunk_evidence(
    pagination_repository: BackendSearchRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep private vector-hook compatibility when its evidence is not score-ordered."""
    repo = pagination_repository
    reranker = _FakeReranker({"Note": 0.8})
    repo._rerank_provider = reranker
    chunks = [
        {"chunk_key": "entity:730:0", "chunk_text": "weak", "best_similarity": 0.4},
        *[
            {"chunk_key": f"entity:700:{i}", "chunk_text": "strong", "best_similarity": 0.9}
            for i in range(7)
        ],
        {"chunk_key": "entity:730:1", "chunk_text": "later strong", "best_similarity": 0.8},
    ]

    async def vector_query(
        session: AsyncSession,
        embedding: list[float],
        candidate_limit: int,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        return chunks[:candidate_limit]

    monkeypatch.setattr(repo, "_run_vector_query", vector_query)
    rows = await repo.search(
        search_text="auth", retrieval_mode=SearchRetrievalMode.VECTOR, limit=3, min_similarity=0.5
    )
    assert [row.id for row in rows] == [700, 730]
    assert len(reranker.document_batches[0]) == 1


@pytest.mark.asyncio
async def test_pending_manifest_slots_preserve_backend_prefix_membership(
    pagination_repository: BackendSearchRepository,
) -> None:
    repo = pagination_repository
    reranker = _FakeReranker({"Note": 0.8})
    repo._rerank_provider = reranker
    async with repo.session_maker() as session:
        matches = await repo._run_vector_query(session, [1.0, 0.0, 0.0, 0.0], 8)
    assert len(matches) == 8
    async with db.scoped_session(repo.session_maker) as session:
        await session.execute(
            text(
                "UPDATE search_vector_chunks SET embedding_status = 'pending' "
                "WHERE project_id = :project_id AND chunk_key IN :chunk_keys"
            ).bindparams(bindparam("chunk_keys", expanding=True)),
            {
                "project_id": repo.project_id,
                "chunk_keys": [row["chunk_key"] for row in matches[:7]],
            },
        )
        await session.commit()
    for size in (2, 20):
        rows = await repo.search(
            search_text="auth", retrieval_mode=SearchRetrievalMode.VECTOR, limit=size
        )
        assert rows
    assert reranker.document_batches[0] == reranker.document_batches[1]
    # SQLite filters after nearest-neighbor selection; Postgres filters before it.
    expected = 1 if repo._app_config.database_backend == DatabaseBackend.SQLITE else 2
    assert len(reranker.document_batches[0]) == expected
