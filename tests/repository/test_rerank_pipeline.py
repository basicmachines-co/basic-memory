"""Rerank stage wiring in the shared search pipeline (vector + hybrid)."""

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.semantic_errors import (
    RerankProviderContractError,
    SemanticDependenciesMissingError,
)
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode


class _StubEmbeddingProvider:
    """Deterministic embeddings that give the two auth notes DIFFERENT similarity.

    An "auth" doc containing "deep" is tilted slightly off the query axis, so vector
    retrieval ranks the plain-auth note strictly above it. That makes the pre-rerank
    baseline a real ordering (not a tie), so a rerank that promotes the lower note is
    a genuine "recover the below-cutoff doc" scenario (#950), not a coin flip.
    """

    model_name = "stub"
    dimensions = 4

    async def embed_query(self, text: str) -> list[float]:
        return self._vectorize(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vectorize(t) for t in texts]

    def runtime_log_attrs(self) -> dict:
        return {}

    @staticmethod
    def _vectorize(text: str) -> list[float]:
        lowered = text.lower()
        if "auth" not in lowered:
            return [0.0, 0.0, 0.0, 1.0]
        # Unit vectors; cos with the query axis [1,0,0,0] is 1.0 vs 0.9.
        if "deep" in lowered:
            return [0.9, 0.4358898943540674, 0.0, 0.0]
        return [1.0, 0.0, 0.0, 0.0]


class _FakeReranker:
    """Scores a document by the marker substring it contains; records call count."""

    model_name = "fake-reranker"

    def __init__(self, score_by_marker: dict[str, float]):
        self.score_by_marker = score_by_marker
        self.calls = 0

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls += 1
        scores = []
        for doc in documents:
            score = 0.0
            for marker, value in self.score_by_marker.items():
                if marker in doc:
                    score = value
            scores.append(score)
        return scores

    def runtime_log_attrs(self) -> dict:
        return {}


class _BadReranker:
    model_name = "bad"

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        return []  # deliberately misaligned (no exception)

    def runtime_log_attrs(self) -> dict:
        return {}


class _ExplodingReranker:
    """Transient failure: a plain error the pipeline should degrade past."""

    model_name = "boom"

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        raise RuntimeError("cross-encoder backend unreachable")

    def runtime_log_attrs(self) -> dict:
        return {}


class _PermanentFaultReranker:
    """Permanent fault (bad config/deps): the pipeline must surface, not swallow."""

    model_name = "permanent"

    def __init__(self, exc: Exception):
        self._exc = exc

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        raise self._exc

    def runtime_log_attrs(self) -> dict:
        return {}


def _entity_row(*, project_id: int, row_id: int, title: str, permalink: str, content: str):
    now = datetime.now(timezone.utc)
    return SearchIndexRow(
        project_id=project_id,
        id=row_id,
        type=SearchItemType.ENTITY.value,
        title=title,
        permalink=permalink,
        file_path=f"{permalink}.md",
        metadata={"note_type": "spec"},
        entity_id=row_id,
        content_stems=content,
        content_snippet=content,
        created_at=now,
        updated_at=now,
    )


def _row(**overrides) -> SearchIndexRow:
    now = datetime.now(timezone.utc)
    base: dict[str, Any] = dict(
        project_id=1,
        id=1,
        type=SearchItemType.ENTITY.value,
        file_path="x.md",
        created_at=now,
        updated_at=now,
        title="Title",
        content_snippet="snippet",
        score=0.5,
    )
    base.update(overrides)
    return SearchIndexRow(**base)


def _unit_repo() -> SQLiteSearchRepository:
    """A repo built without a real DB — for the pure rerank helper methods."""
    config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/test"},
        default_project="test-project",
        database_backend=DatabaseBackend.SQLITE,
        semantic_search_enabled=True,
    )
    return SQLiteSearchRepository(
        MagicMock(),
        project_id=1,
        app_config=config,
        embedding_provider=_StubEmbeddingProvider(),
    )


# --- Pure helper behavior ---


def test_should_rerank_gating():
    repo = _unit_repo()
    repo._rerank_provider = None
    assert repo._should_rerank("auth") is False
    repo._rerank_provider = _FakeReranker({})
    assert repo._should_rerank("") is False
    assert repo._should_rerank("auth") is True


def test_rerank_document_text_fallbacks():
    repo = _unit_repo()
    assert repo._rerank_document_text(_row(title="T", matched_chunk_text="chunk")) == "T\nchunk"
    assert (
        repo._rerank_document_text(_row(title="T", matched_chunk_text=None, content_snippet="snip"))
        == "T\nsnip"
    )
    assert (
        repo._rerank_document_text(_row(title=None, matched_chunk_text="only-body")) == "only-body"
    )
    assert (
        repo._rerank_document_text(_row(title="only-title", content_snippet=None)) == "only-title"
    )
    assert repo._rerank_document_text(_row(title=None, content_snippet=None)) == ""


def test_rerank_document_text_truncation():
    repo = _unit_repo()
    row = _row(title="T", matched_chunk_text="x" * 500)  # full text = "T\n" + 500 = 502 chars
    repo._reranker_max_document_chars = 0  # disabled
    assert len(repo._rerank_document_text(row)) == 502
    repo._reranker_max_document_chars = 100  # trims to the leading (most-relevant) text
    trimmed = repo._rerank_document_text(row)
    assert len(trimmed) == 100 and trimmed.startswith("T\nxxx")
    repo._reranker_max_document_chars = 10_000  # no-op when already under the cap
    assert len(repo._rerank_document_text(row)) == 502


def test_candidate_limit_over_fetches_chunks_for_rerank_pool():
    """With reranking active, over-fetch chunks so dedup can't starve the rerank window."""
    from basic_memory.repository.search_repository_base import RERANK_POOL_CHUNK_FANOUT

    repo = _unit_repo()
    repo._semantic_vector_k = 5
    repo._reranker_candidates = 20

    repo._rerank_provider = None
    assert repo._candidate_limit(limit=1, offset=0, query_text="auth") == 10  # max(5, 10)

    repo._rerank_provider = _FakeReranker({})
    assert (
        repo._candidate_limit(limit=1, offset=0, query_text="auth") == 20 * RERANK_POOL_CHUNK_FANOUT
    )
    assert repo._candidate_limit(limit=1, offset=0, query_text="") == 10  # no query → no bump


@pytest.mark.asyncio
async def test_rerank_paginate_noop_paths():
    repo = _unit_repo()
    rows = [_row(id=1), _row(id=2)]
    repo._rerank_provider = None
    assert await repo._rerank_and_paginate("auth", rows, offset=0, limit=10) == rows

    repo._rerank_provider = _FakeReranker({})
    single = [_row(id=1)]
    assert await repo._rerank_and_paginate("auth", single, offset=0, limit=10) == single


@pytest.mark.asyncio
async def test_rerank_paginate_reorders_rescore_and_demotes_tail():
    repo = _unit_repo()
    repo._rerank_provider = _FakeReranker({"Alpha": 0.1, "Bravo": 0.9, "Charlie": 0.5})
    repo._reranker_candidates = 2
    rows = [
        _row(id=1, title="Alpha"),
        _row(id=2, title="Bravo"),
        _row(id=3, title="Charlie"),  # past the pool
    ]

    result = await repo._rerank_and_paginate("auth", rows, offset=0, limit=3)

    assert [r.title for r in result] == ["Bravo", "Alpha", "Charlie"]
    assert result[0].score == 0.9  # reranker relevance replaces the prior score
    # Tail is demoted strictly below the reranked floor (0.1) so the page stays
    # monotonic and in [0, 1] — no scale mixing.
    scores = [r.score for r in result if r.score is not None]
    assert len(scores) == 3
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert result[2].title == "Charlie"
    assert scores[2] < scores[1]


@pytest.mark.asyncio
async def test_rerank_paginate_skips_provider_on_deep_page():
    """A page entirely past the rerank pool must not spend a (possibly paid) call."""
    repo = _unit_repo()
    reranker = _FakeReranker({"Alpha": 0.9})
    repo._rerank_provider = reranker
    repo._reranker_candidates = 2
    rows = [_row(id=i, title=f"n{i}") for i in range(5)]

    result = await repo._rerank_and_paginate("auth", rows, offset=2, limit=2)

    assert reranker.calls == 0
    assert [r.id for r in result] == [2, 3]  # plain retrieval slice


@pytest.mark.asyncio
async def test_rerank_paginate_degrades_gracefully_on_provider_error():
    """A reranker exception falls back to retrieval order instead of failing search."""
    repo = _unit_repo()
    repo._rerank_provider = _ExplodingReranker()
    repo._reranker_candidates = 20
    rows = [_row(id=1, title="A"), _row(id=2, title="B")]

    result = await repo._rerank_and_paginate("auth", rows, offset=0, limit=10)

    assert [r.id for r in result] == [1, 2]  # unchanged retrieval order


@pytest.mark.asyncio
async def test_rerank_paginate_misaligned_scores_raise():
    """A length mismatch is a provider bug — fail fast, don't degrade."""
    repo = _unit_repo()
    repo._rerank_provider = _BadReranker()
    repo._reranker_candidates = 20
    with pytest.raises(RerankProviderContractError, match="Reranker returned 0 scores"):
        await repo._rerank_and_paginate("auth", [_row(id=1), _row(id=2)], offset=0, limit=10)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        RerankProviderContractError("incomplete rerank response"),
        SemanticDependenciesMissingError("fastembed missing"),
    ],
)
async def test_rerank_paginate_surfaces_permanent_faults(exc):
    """Permanent faults (contract break, missing deps) propagate — not silently degraded."""
    repo = _unit_repo()
    repo._rerank_provider = _PermanentFaultReranker(exc)
    repo._reranker_candidates = 20
    with pytest.raises(type(exc)):
        await repo._rerank_and_paginate("auth", [_row(id=1), _row(id=2)], offset=0, limit=10)


# --- End-to-end through the SQLite repo ---


def _enable_semantic(repo: SQLiteSearchRepository) -> None:
    try:
        import sqlite_vec  # noqa: F401
    except ImportError:  # pragma: no cover
        pytest.skip("sqlite-vec dependency is required for vector search tests.")
    repo._semantic_enabled = True
    repo._embedding_provider = _StubEmbeddingProvider()
    repo._vector_dimensions = 4
    repo._vector_tables_initialized = False
    repo._semantic_min_similarity = 0.0


async def _index_two_auth_notes(repo: SQLiteSearchRepository) -> None:
    await repo.init_search_index()
    await repo.bulk_index_items(
        [
            _entity_row(
                project_id=repo.project_id,
                row_id=401,
                title="Alpha Auth Guide",
                permalink="specs/alpha",
                content="auth login session token overview",
            ),
            _entity_row(
                project_id=repo.project_id,
                row_id=402,
                title="Bravo Auth Guide",
                permalink="specs/bravo",
                content="auth login session token deep dive",
            ),
        ]
    )
    await repo.sync_entity_vectors(401)
    await repo.sync_entity_vectors(402)


@pytest.mark.asyncio
async def test_vector_search_applies_reranker(search_repository):
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("sqlite-vec repository behavior is local SQLite-only.")

    _enable_semantic(search_repository)
    await _index_two_auth_notes(search_repository)
    # Promote the note that vector similarity alone leaves tied/second.
    reranker = _FakeReranker({"Alpha": 0.1, "Bravo": 0.9})
    search_repository._rerank_provider = reranker

    results = await search_repository.search(
        search_text="auth session token",
        retrieval_mode=SearchRetrievalMode.VECTOR,
        limit=5,
    )

    # Baseline vector order is alpha > bravo (see test_search_without_reranker);
    # the reranker promotes bravo — a genuine below-cutoff recovery, not a tie-break.
    assert [r.permalink for r in results[:2]] == ["specs/bravo", "specs/alpha"]
    assert results[0].score == 0.9
    scores = [r.score for r in results if r.score is not None]
    assert scores == sorted(scores, reverse=True)
    assert reranker.calls == 1


@pytest.mark.asyncio
async def test_hybrid_search_reranks_once(search_repository):
    """Hybrid reranks the fused result exactly once — not again inside its vector leg."""
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("sqlite-vec repository behavior is local SQLite-only.")

    _enable_semantic(search_repository)
    await _index_two_auth_notes(search_repository)
    reranker = _FakeReranker({"Alpha": 0.1, "Bravo": 0.9})
    search_repository._rerank_provider = reranker

    results = await search_repository.search(
        search_text="auth session token",
        retrieval_mode=SearchRetrievalMode.HYBRID,
        limit=5,
    )

    assert results[0].permalink == "specs/bravo"
    assert results[0].score == 0.9
    scores = [r.score for r in results if r.score is not None]
    assert scores == sorted(scores, reverse=True)
    assert reranker.calls == 1


@pytest.mark.asyncio
async def test_hybrid_search_degrades_on_reranker_error(search_repository):
    """A failing reranker must not take the whole search down."""
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("sqlite-vec repository behavior is local SQLite-only.")

    _enable_semantic(search_repository)
    await _index_two_auth_notes(search_repository)
    search_repository._rerank_provider = _ExplodingReranker()

    results = await search_repository.search(
        search_text="auth session token",
        retrieval_mode=SearchRetrievalMode.HYBRID,
        limit=5,
    )
    # Falls back to retrieval order; results still returned, no exception.
    assert {r.permalink for r in results} == {"specs/alpha", "specs/bravo"}


@pytest.mark.asyncio
async def test_hybrid_search_propagates_contract_error(search_repository):
    """A provider-contract break (e.g. incomplete rerank response) must surface, not hide."""
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("sqlite-vec repository behavior is local SQLite-only.")

    _enable_semantic(search_repository)
    await _index_two_auth_notes(search_repository)
    search_repository._rerank_provider = _PermanentFaultReranker(
        RerankProviderContractError("incomplete rerank response")
    )

    with pytest.raises(RerankProviderContractError):
        await search_repository.search(
            search_text="auth session token",
            retrieval_mode=SearchRetrievalMode.HYBRID,
            limit=5,
        )


@pytest.mark.asyncio
async def test_search_without_reranker_keeps_baseline(search_repository):
    if not isinstance(search_repository, SQLiteSearchRepository):
        pytest.skip("sqlite-vec repository behavior is local SQLite-only.")

    _enable_semantic(search_repository)
    await _index_two_auth_notes(search_repository)
    search_repository._rerank_provider = None

    results = await search_repository.search(
        search_text="auth session token",
        retrieval_mode=SearchRetrievalMode.VECTOR,
        limit=5,
    )
    # Vector similarity ranks the plain-auth note above the "deep"-tilted one.
    assert [r.permalink for r in results] == ["specs/alpha", "specs/bravo"]
