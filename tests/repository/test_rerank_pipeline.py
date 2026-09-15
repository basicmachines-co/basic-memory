"""Rerank stage wiring in the shared search pipeline (vector + hybrid)."""

from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from logfire.testing import CaptureLogfire, capfire as capfire

import basic_memory.repository.postgres_search_repository as postgres_search_repository_module
import basic_memory.repository.search_repository as search_repository_module
import basic_memory.repository.sqlite_search_repository as sqlite_search_repository_module
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.api.v2.utils import to_search_results
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.rerank_provider import (
    build_rerank_document,
    demote_tail_scores,
    validate_rerank_scores,
)
from basic_memory.repository.search_filters import FtsBackend
from basic_memory.repository.search_query import PreparedSearchQuery
from basic_memory.repository.search_reader import (
    RERANK_POOL_CHUNK_FANOUT,
    HydratedChunk,
    Reranking,
    SemanticSearch,
    VectorRetrieval,
    rerank_document_text,
)
from basic_memory.repository.search_repository import create_search_repository
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.semantic_errors import (
    RerankProviderContractError,
    RerankTransientError,
    SemanticDependenciesMissingError,
)
from basic_memory.repository.semantic_vector_index import SemanticVectorIndex
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode
from basic_memory.services.entity_service import EntityService
from tests.repository.test_hybrid_fusion import FakeFts

type BackendSearchRepository = SQLiteSearchRepository | PostgresSearchRepository


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

    def runtime_log_attrs(self) -> dict[str, Any]:
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
        self.document_batches: list[list[str]] = []

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls += 1
        self.document_batches.append(documents)
        scores = []
        for doc in documents:
            score = 0.0
            for marker, value in self.score_by_marker.items():
                if marker in doc:
                    score = value
            scores.append(score)
        return scores

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {}


class _BadReranker:
    model_name = "bad"

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        return []  # deliberately misaligned (no exception)

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {}


class _ExplodingReranker:
    """Typed transient failure the pipeline must surface."""

    model_name = "boom"

    def __init__(self) -> None:
        self.calls = 0

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls += 1
        raise RerankTransientError("cross-encoder backend unreachable")

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {}


class _SucceedsThenTransientReranker:
    """Rerank one page, then model a temporary provider outage."""

    model_name = "flaky"

    def __init__(self):
        self.calls = 0

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls += 1
        if self.calls > 1:
            raise RerankTransientError("cross-encoder backend unreachable")
        return [0.1, 0.9]

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {}


class _PermanentFaultReranker:
    """Permanent fault (bad config/deps): the pipeline must surface, not swallow."""

    model_name = "permanent"

    def __init__(self, exc: Exception):
        self._exc = exc

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        raise self._exc

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {}


def test_validate_rerank_scores_rejects_non_numeric_value():
    """Provider contract validation rejects values that cannot become finite floats."""
    with pytest.raises(RerankProviderContractError, match="is not a number"):
        validate_rerank_scores(["not-a-score"], expected_count=1)


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


def _reranking(provider: Any, *, candidates: int = 20, max_document_chars: int = 0) -> Reranking:
    return Reranking(
        provider=provider, candidates=candidates, max_document_chars=max_document_chars
    )


def _semantic(
    *,
    vector_k: int = 100,
    rerank: Reranking | None = None,
    fts: FtsBackend | None = None,
) -> SemanticSearch:
    """A pipeline built without a real DB, for the pure rerank helpers."""
    vector = VectorRetrieval(
        index=cast(SemanticVectorIndex, object()),
        index_name="sqlite-vec",
        embedding_provider=_StubEmbeddingProvider(),
        embedding_model="stub:4",
        vector_k=vector_k,
        min_similarity=0.0,
    )
    return SemanticSearch(MagicMock(), ProjectScope.single(1), fts or FakeFts(), vector, rerank)


# --- Pure helper behavior ---


def test_rerank_is_active_only_with_a_provider_and_a_query():
    assert _semantic()._active_rerank("auth") is None
    semantic = _semantic(rerank=_reranking(_FakeReranker({})))
    assert semantic._active_rerank("") is None
    assert semantic._active_rerank("auth") is semantic.rerank


def test_rerank_document_text_fallbacks():
    assert rerank_document_text(_row(title="T", matched_chunk_text="chunk"), 0) == "chunk\nT"
    assert (
        rerank_document_text(_row(title="T", matched_chunk_text=None, content_snippet="snip"), 0)
        == "snip\nT"
    )
    assert rerank_document_text(_row(title=None, matched_chunk_text="only-body"), 0) == "only-body"
    assert rerank_document_text(_row(title="only-title", content_snippet=None), 0) == "only-title"
    assert rerank_document_text(_row(title=None, content_snippet=None), 0) == ""


def test_rerank_document_text_truncation():
    row = _row(title="T", matched_chunk_text="x" * 500)  # full text = 500 + "\nT" = 502 chars
    assert len(rerank_document_text(row, 0)) == 502  # disabled
    trimmed = rerank_document_text(row, 100)  # trims to the leading (most-relevant) text
    assert len(trimmed) == 100 and trimmed == "x" * 100
    assert len(rerank_document_text(row, 10_000)) == 502  # no-op when already under the cap


def test_rerank_document_text_cap_preserves_matched_body_with_long_title():
    row = _row(title="title-" * 20, matched_chunk_text="MATCHED body")

    assert "MATCHED" in rerank_document_text(row, 8)


def test_demoted_tail_scores_are_stable_as_the_tail_grows():
    """An existing tail rank keeps its score when later results are fetched."""
    first_page_scores = demote_tail_scores(floor=0.2, count=2)
    growing_prefix_scores = demote_tail_scores(floor=0.2, count=5)

    assert growing_prefix_scores[:2] == first_page_scores
    assert growing_prefix_scores == sorted(growing_prefix_scores, reverse=True)
    assert all(0.0 < score < 0.2 for score in growing_prefix_scores)


def test_candidate_limit_over_fetches_chunks_for_rerank_pool():
    """With reranking active, over-fetch chunks so dedup can't starve the rerank window."""
    plain = _semantic(vector_k=5)
    assert plain._candidate_limit(limit=1, offset=0, query_text="auth") == 10  # max(5, 10)

    reranked = _semantic(vector_k=5, rerank=_reranking(_FakeReranker({}), candidates=20))
    assert (
        reranked._candidate_limit(limit=1, offset=0, query_text="auth")
        == 20 * RERANK_POOL_CHUNK_FANOUT
    )
    assert reranked._candidate_limit(limit=1, offset=0, query_text="") == 10  # no query → no bump


def test_candidate_limit_expands_only_for_results_beyond_rerank_pool():
    """The fixed rerank window grows only enough to supply the requested tail."""
    semantic = _semantic(vector_k=5, rerank=_reranking(_FakeReranker({}), candidates=20))

    first_page_limit = semantic._candidate_limit(limit=10, offset=0, query_text="auth")

    assert first_page_limit == 20 * RERANK_POOL_CHUNK_FANOUT
    assert semantic._candidate_limit(limit=20, offset=0, query_text="auth") == first_page_limit
    assert semantic._candidate_limit(limit=10, offset=10, query_text="auth") == first_page_limit
    assert semantic._candidate_limit(limit=21, offset=0, query_text="auth") == 90
    assert semantic._candidate_limit(limit=10, offset=19, query_text="auth") == 170
    assert semantic._candidate_limit(limit=10, offset=20, query_text="auth") == 180
    # Large first pages still retrieve their untouched tail and pagination probe.
    assert semantic._candidate_limit(limit=101, offset=0, query_text="auth") == 890


@pytest.mark.asyncio
async def test_rerank_paginate_noop_paths():
    rows = [_row(id=1), _row(id=2)]
    assert await _semantic()._rerank_and_paginate("auth", rows, offset=0, limit=10) == rows

    reranker = _FakeReranker({})
    semantic = _semantic(rerank=_reranking(reranker))
    assert await semantic._rerank_and_paginate("", rows, offset=0, limit=10) == rows
    assert await semantic._rerank_and_paginate("auth", [], offset=0, limit=10) == []
    assert await semantic._rerank_and_paginate("auth", rows, offset=2, limit=10) == []
    assert reranker.calls == 0


@pytest.mark.asyncio
async def test_rerank_paginate_reorders_rescore_and_demotes_tail():
    semantic = _semantic(
        rerank=_reranking(_FakeReranker({"Alpha": 0.1, "Bravo": 0.9, "Charlie": 0.5}), candidates=2)
    )
    rows = [
        _row(id=1, title="Alpha"),
        _row(id=2, title="Bravo"),
        _row(id=3, title="Charlie"),  # past the pool
    ]

    result = await semantic._rerank_and_paginate("auth", rows, offset=0, limit=3)

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
async def test_rerank_paginate_preserves_pool_before_tail_at_zero_floor():
    semantic = _semantic(
        rerank=_reranking(_FakeReranker({"Alpha": 0.0, "Bravo": 0.9}), candidates=2)
    )
    rows = [
        _row(id=1, title="Alpha"),
        _row(id=2, title="Bravo"),
        _row(id=3, title="Charlie"),
    ]

    result = await semantic._rerank_and_paginate("auth", rows, offset=0, limit=3)

    assert [row.title for row in result] == ["Bravo", "Alpha", "Charlie"]
    assert [row.score for row in result] == [0.9, 0.0, 0.0]


@pytest.mark.asyncio
async def test_rerank_paginate_scores_singleton_prefix_and_demotes_tail():
    """A one-row prefix still calibrates scores before cross-project merging."""
    reranker = _FakeReranker({"Only": 0.4})
    semantic = _semantic(rerank=_reranking(reranker, candidates=2))
    stable_rows = [_row(id=1, title="Only", score=0.5)]
    expanded_rows = [
        stable_rows[0],
        _row(id=2, title="Tail", score=1.3),
    ]

    result = await semantic._rerank_and_paginate(
        "auth",
        expanded_rows,
        offset=0,
        limit=2,
        stable_rows=stable_rows,
    )

    assert reranker.calls == 1
    assert [row.title for row in result] == ["Only", "Tail"]
    assert result[0].score == 0.4
    assert result[1].score is not None and result[1].score < 0.4


@pytest.mark.asyncio
async def test_rerank_paginate_calibrates_tail_scores_on_deep_page():
    """Deep pages rescore the fixed prefix before returning its calibrated tail."""
    reranker = _FakeReranker({"n1": 0.9, "n2": 0.8})
    semantic = _semantic(rerank=_reranking(reranker, candidates=2))
    stable_rows = [_row(id=1, title="n1"), _row(id=2, title="n2")]
    expanded_rows = [
        _row(id=3, title="newly strengthened"),
        stable_rows[0],
        stable_rows[1],
        _row(id=4, title="n4"),
        _row(id=5, title="n5"),
    ]

    result = await semantic._rerank_and_paginate(
        "auth",
        expanded_rows,
        offset=2,
        limit=2,
        stable_rows=stable_rows,
    )

    assert reranker.calls == 1
    assert [r.id for r in result] == [3, 4]
    assert all(row.score is not None and row.score < 0.8 for row in result)


@pytest.mark.asyncio
async def test_rerank_paginate_keeps_expanded_candidates_out_of_stable_prefix():
    """A larger tail retrieval cannot replace candidates in the reranked prefix."""
    reranker = _FakeReranker({"Alpha": 0.1, "Bravo": 0.9, "Charlie": 1.0})
    semantic = _semantic(rerank=_reranking(reranker, candidates=2))
    stable_rows = [_row(id=1, title="Alpha"), _row(id=2, title="Bravo")]
    expanded_rows = [
        _row(id=3, title="Charlie"),
        stable_rows[0],
        stable_rows[1],
        _row(id=4, title="Delta"),
    ]

    result = await semantic._rerank_and_paginate(
        "auth",
        expanded_rows,
        offset=0,
        limit=3,
        stable_rows=stable_rows,
    )
    expanded_result = await semantic._rerank_and_paginate(
        "auth",
        expanded_rows + [_row(id=5, title="Echo"), _row(id=6, title="Foxtrot")],
        offset=0,
        limit=3,
        stable_rows=stable_rows,
    )

    assert [row.title for row in result] == ["Bravo", "Alpha", "Charlie"]
    assert [row.title for row in expanded_result] == ["Bravo", "Alpha", "Charlie"]
    assert [row.score for row in expanded_result] == [row.score for row in result]
    assert reranker.calls == 2


@pytest.mark.asyncio
async def test_rerank_paginate_surfaces_transient_provider_error():
    """Transient failures must not silently replace reranked order with retrieval order."""
    semantic = _semantic(rerank=_reranking(_ExplodingReranker(), candidates=20))
    rows = [_row(id=1, title="A"), _row(id=2, title="B")]

    with pytest.raises(RerankTransientError, match="backend unreachable"):
        await semantic._rerank_and_paginate("auth", rows, offset=0, limit=10)


@pytest.mark.asyncio
async def test_rerank_paginate_does_not_duplicate_results_when_later_page_is_transient():
    """A later page fails instead of changing order and repeating an earlier result."""
    semantic = _semantic(rerank=_reranking(_SucceedsThenTransientReranker(), candidates=2))
    rows = [_row(id=1, title="A"), _row(id=2, title="B")]

    first_page = await semantic._rerank_and_paginate("auth", rows, offset=0, limit=1)

    assert [row.id for row in first_page] == [2]
    with pytest.raises(RerankTransientError, match="backend unreachable"):
        await semantic._rerank_and_paginate("auth", rows, offset=1, limit=1)


@pytest.mark.asyncio
async def test_rerank_paginate_misaligned_scores_raise():
    """A length mismatch is a provider bug — fail fast, don't degrade."""
    semantic = _semantic(rerank=_reranking(_BadReranker(), candidates=20))
    with pytest.raises(RerankProviderContractError, match="Reranker returned 0 scores"):
        await semantic._rerank_and_paginate("auth", [_row(id=1), _row(id=2)], offset=0, limit=10)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        RerankProviderContractError("incomplete rerank response"),
        SemanticDependenciesMissingError("fastembed missing"),
        RuntimeError("unexpected provider failure"),
    ],
)
async def test_rerank_paginate_surfaces_permanent_faults(exc):
    """Permanent faults (contract break, missing deps) propagate — not silently degraded."""
    semantic = _semantic(rerank=_reranking(_PermanentFaultReranker(exc), candidates=20))
    with pytest.raises(type(exc)):
        await semantic._rerank_and_paginate("auth", [_row(id=1), _row(id=2)], offset=0, limit=10)


# --- End-to-end through both repository backends ---


def _semantic_search_repository(
    session_maker: Any,
    project_id: int,
    app_config: BasicMemoryConfig,
    **config_updates: object,
) -> BackendSearchRepository:
    config = app_config.model_copy(
        update={
            "semantic_search_enabled": True,
            "semantic_min_similarity": 0.0,
            **config_updates,
        }
    )
    repository_type = (
        PostgresSearchRepository
        if config.database_backend == DatabaseBackend.POSTGRES
        else SQLiteSearchRepository
    )
    return repository_type(
        session_maker,
        project_id=project_id,
        app_config=config,
        embedding_provider=_StubEmbeddingProvider(),
    )


@pytest.fixture
def rerank_search_repository(
    session_maker: Any,
    test_project: Any,
    app_config: BasicMemoryConfig,
) -> BackendSearchRepository:
    return _semantic_search_repository(session_maker, test_project.id, app_config)


async def _index_two_auth_notes(repo: BackendSearchRepository) -> None:
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
async def test_vector_search_applies_reranker(rerank_search_repository):
    await _index_two_auth_notes(rerank_search_repository)
    # Promote the note that vector similarity alone leaves tied/second.
    reranker = _FakeReranker({"Alpha": 0.1, "Bravo": 0.9})
    rerank_search_repository._rerank_provider = reranker

    results = await rerank_search_repository.search(
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
async def test_vector_search_expands_tail_from_stable_rerank_pool(
    rerank_search_repository,
    monkeypatch,
):
    """Vector retrieval keeps its fixed prefix when a request also needs tail rows."""
    await _index_two_auth_notes(rerank_search_repository)
    rerank_search_repository._semantic_vector_k = 5
    rerank_search_repository._reranker_candidates = 2
    rerank_search_repository._rerank_provider = _FakeReranker({"Alpha": 0.1, "Bravo": 0.9})

    candidate_limits: list[int] = []
    run_vector_query = SemanticSearch._run_vector_query

    async def record_vector_query(
        self: SemanticSearch,
        session: Any,
        query_embedding: list[float],
        candidate_limit: int,
        *,
        trace: Any = None,
    ) -> list[HydratedChunk]:
        candidate_limits.append(candidate_limit)
        return await run_vector_query(self, session, query_embedding, candidate_limit, trace=trace)

    monkeypatch.setattr(SemanticSearch, "_run_vector_query", record_vector_query)

    results = await rerank_search_repository.search(
        search_text="auth session token",
        retrieval_mode=SearchRetrievalMode.VECTOR,
        limit=3,
    )

    assert [row.permalink for row in results] == ["specs/bravo", "specs/alpha"]
    assert candidate_limits == [18, 8]


@pytest.mark.asyncio
async def test_vector_slow_query_timing_includes_reranker(rerank_search_repository, monkeypatch):
    await _index_two_auth_notes(rerank_search_repository)
    clock = {"now": 0.0}
    reranker = _FakeReranker({"Alpha": 0.1, "Bravo": 0.9})
    original_rerank = reranker.rerank

    async def slow_rerank(query: str, documents: list[str]) -> list[float]:
        clock["now"] = 3.0
        return await original_rerank(query, documents)

    rerank_search_repository._rerank_provider = reranker
    monkeypatch.setattr(reranker, "rerank", slow_rerank)
    monkeypatch.setattr(
        "basic_memory.repository.search_reader.time.perf_counter",
        lambda: clock["now"],
    )
    warning = MagicMock()
    monkeypatch.setattr(
        "basic_memory.repository.search_reader.logger.warning",
        warning,
    )

    await rerank_search_repository.search(
        search_text="auth session token",
        retrieval_mode=SearchRetrievalMode.VECTOR,
        limit=5,
    )

    warning.assert_called_once()
    assert warning.call_args.args[0].startswith("[SEMANTIC_SLOW_QUERY]")
    assert warning.call_args.kwargs["total_ms"] == 3000.0


@pytest.mark.asyncio
async def test_hybrid_search_reranks_once(rerank_search_repository):
    """Hybrid reranks the fused result exactly once — not again inside its vector leg."""
    await _index_two_auth_notes(rerank_search_repository)
    reranker = _FakeReranker({"Alpha": 0.1, "Bravo": 0.9})
    rerank_search_repository._rerank_provider = reranker

    results = await rerank_search_repository.search(
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
@pytest.mark.parametrize("rerank_enabled", [False, True])
async def test_hybrid_fts_only_preview_stays_bounded_through_api_hydration(
    rerank_search_repository: BackendSearchRepository,
    entity_service: EntityService,
    rerank_enabled: bool,
    capfire: CaptureLogfire,
) -> None:
    """Large lexical-only notes cannot bypass the public preview via matched_chunk."""
    repo = rerank_search_repository
    await _index_two_auth_notes(repo)
    content = "auth session token archive " * 5_000
    await repo.index_item(
        _entity_row(
            project_id=repo.project_id,
            row_id=403,
            title="Auth archive",
            permalink="specs/archive",
            content=content,
        )
    )
    # Leave this note unembedded so its real FTS hit has no vector chunk.
    reranker = _FakeReranker({"archive": 0.95, "Alpha": 0.8, "Bravo": 0.7})
    repo._rerank_provider = reranker if rerank_enabled else None
    capfire.exporter.clear()

    rows = await repo.search(
        search_text="auth session token",
        retrieval_mode=SearchRetrievalMode.HYBRID,
        limit=5,
    )
    results = await to_search_results(entity_service, rows)

    lexical = next(result for result in results if result.permalink == "specs/archive")
    assert lexical.content == content[:4000]
    assert lexical.content_length == len(content)
    assert lexical.content_truncated is True
    assert lexical.matched_chunk is None
    assert len(lexical.model_dump_json().encode("utf-8")) < 6000
    vector = next(result for result in results if result.permalink == "specs/alpha")
    assert vector.matched_chunk == "auth login session token overview"
    if rerank_enabled:
        assert reranker.calls == 1
        assert all(len(document) <= 2000 for document in reranker.document_batches[0])

    spans = capfire.exporter.exported_spans_as_dict()
    names = {span["name"] for span in spans}
    assert {
        "search.fts",
        "search.embed_query",
        "search.vector_query",
        "search.vector_manifest_hydration",
        "search.fetch_candidate_rows",
        "search.fusion",
        "search.hydrate_results",
    } <= names
    assert ("search.rerank" in names) is rerank_enabled
    fusion = next(span for span in spans if span["name"] == "search.fusion")
    assert fusion["attributes"]["result_count"] == 3
    assert all("auth session token" not in str(span["attributes"]) for span in spans)


@pytest.mark.asyncio
async def test_hybrid_search_preserves_candidate_windows(
    rerank_search_repository,
    monkeypatch,
):
    """Hybrid preserves legacy recall unless reranking owns the shared candidate pool."""
    await _index_two_auth_notes(rerank_search_repository)
    rerank_search_repository._semantic_vector_k = 100
    rerank_search_repository._reranker_candidates = 100
    rerank_search_repository._rerank_provider = None

    candidate_limits: list[int] = []
    run_vector_query = SemanticSearch._run_vector_query

    async def record_vector_query(
        self: SemanticSearch,
        session: Any,
        query_embedding: list[float],
        candidate_limit: int,
        *,
        trace: Any = None,
    ) -> list[HydratedChunk]:
        candidate_limits.append(candidate_limit)
        return await run_vector_query(self, session, query_embedding, candidate_limit, trace=trace)

    monkeypatch.setattr(SemanticSearch, "_run_vector_query", record_vector_query)

    baseline_results = await rerank_search_repository.search(
        search_text="auth session token",
        retrieval_mode=SearchRetrievalMode.HYBRID,
        limit=10,
    )

    assert baseline_results
    assert candidate_limits == [1000]

    candidate_limits.clear()
    rerank_search_repository._semantic_vector_k = 5
    rerank_search_repository._reranker_candidates = 20
    reranker = _FakeReranker({"Alpha": 0.1, "Bravo": 0.9})
    rerank_search_repository._rerank_provider = reranker
    reranked_results = await rerank_search_repository.search(
        search_text="auth session token",
        retrieval_mode=SearchRetrievalMode.HYBRID,
        limit=11,
    )

    assert reranked_results
    assert candidate_limits == [80]

    candidate_limits.clear()
    growing_prefix_results = await rerank_search_repository.search(
        search_text="auth session token",
        retrieval_mode=SearchRetrievalMode.HYBRID,
        limit=21,
    )

    assert growing_prefix_results
    assert reranker.calls == 2
    assert candidate_limits == [90, 80]


@pytest.mark.asyncio
async def test_hybrid_search_keeps_deep_tail_stable_as_candidate_window_grows(monkeypatch):
    """Late dual-source evidence cannot move a row across an earlier tail page."""
    reranker = _FakeReranker({"Alpha": 0.9, "Bravo": 0.8})

    charlie = _row(id=3, title="Charlie")
    delta = _row(id=4, title="Delta")

    def fts_window(limit: int) -> list[SearchIndexRow]:
        if limit <= 8:
            return [
                _row(id=1, title="Alpha", score=10.0),
                _row(id=2, title="Bravo", score=9.0),
            ]
        return [
            _row(id=1, title="Alpha", score=10.0),
            _row(id=2, title="Bravo", score=9.0),
            _row(id=3, title="Charlie", score=8.0),
            _row(id=4, title="Delta", score=7.0),
        ]

    fts = FakeFts(fts_window)
    semantic = _semantic(vector_k=2, rerank=_reranking(reranker, candidates=2), fts=fts)

    async def fake_vector_search(query: PreparedSearchQuery, **kwargs: Any) -> list[SearchIndexRow]:
        candidate_limit = kwargs["candidate_limit"]
        assert candidate_limit is not None
        if candidate_limit <= 8:
            return [
                _row(id=1, title="Alpha", score=1.0),
                _row(id=2, title="Bravo", score=0.9),
            ]
        if candidate_limit <= 18:
            return [
                _row(id=1, title="Alpha", score=1.0),
                _row(id=2, title="Bravo", score=0.9),
                _row(id=4, title="Delta", score=0.95),
            ]
        return [
            _row(id=1, title="Alpha", score=1.0),
            _row(id=2, title="Bravo", score=0.9),
            _row(id=3, title="Charlie", score=1.0),
            _row(id=4, title="Delta", score=0.7),
        ]

    monkeypatch.setattr(semantic, "vector_only", fake_vector_search)

    async def deep_page(offset: int) -> list[SearchIndexRow]:
        return await semantic.hybrid(
            PreparedSearchQuery(search_text="auth", retrieval_mode=SearchRetrievalMode.HYBRID),
            limit=1,
            offset=offset,
        )

    first_tail_page = await deep_page(offset=2)
    second_tail_page = await deep_page(offset=3)

    assert [row.id for row in first_tail_page] == [charlie.id]
    assert [row.id for row in second_tail_page] == [delta.id]
    assert reranker.calls == 2
    assert all(query.retrieval_mode == SearchRetrievalMode.FTS for query in fts.queries)


@pytest.mark.asyncio
async def test_hybrid_search_surfaces_transient_reranker_error(rerank_search_repository):
    """Hybrid search must not replace reranked order with raw order during an outage."""
    await _index_two_auth_notes(rerank_search_repository)
    rerank_search_repository._rerank_provider = _ExplodingReranker()

    with pytest.raises(RerankTransientError, match="backend unreachable"):
        await rerank_search_repository.search(
            search_text="auth session token",
            retrieval_mode=SearchRetrievalMode.HYBRID,
            limit=5,
        )


@pytest.mark.asyncio
async def test_hybrid_search_propagates_contract_error(rerank_search_repository):
    """A provider-contract break (e.g. incomplete rerank response) must surface, not hide."""
    await _index_two_auth_notes(rerank_search_repository)
    rerank_search_repository._rerank_provider = _PermanentFaultReranker(
        RerankProviderContractError("incomplete rerank response")
    )

    with pytest.raises(RerankProviderContractError):
        await rerank_search_repository.search(
            search_text="auth session token",
            retrieval_mode=SearchRetrievalMode.HYBRID,
            limit=5,
        )


@pytest.mark.asyncio
async def test_search_without_reranker_keeps_baseline(rerank_search_repository):
    await _index_two_auth_notes(rerank_search_repository)
    rerank_search_repository._rerank_provider = None

    results = await rerank_search_repository.search(
        search_text="auth session token",
        retrieval_mode=SearchRetrievalMode.VECTOR,
        limit=5,
    )
    # Vector similarity ranks the plain-auth note above the "deep"-tilted one.
    assert [r.permalink for r in results] == ["specs/alpha", "specs/bravo"]


@pytest.mark.asyncio
async def test_fts_title_and_permalink_searches_never_call_reranker(
    rerank_search_repository,
):
    await _index_two_auth_notes(rerank_search_repository)
    rerank_search_repository._rerank_provider = None
    searches = {
        "text": {"search_text": "auth", "retrieval_mode": SearchRetrievalMode.FTS},
        "title": {"title": "Alpha Auth Guide"},
        "permalink": {"permalink": "specs/alpha"},
    }

    baseline = {
        name: await rerank_search_repository.search(**parameters)
        for name, parameters in searches.items()
    }
    exploding_reranker = _ExplodingReranker()
    rerank_search_repository._rerank_provider = exploding_reranker
    guarded = {
        name: await rerank_search_repository.search(**parameters)
        for name, parameters in searches.items()
    }

    assert all(baseline[name] for name in searches)
    assert guarded == baseline
    assert exploding_reranker.calls == 0


@pytest.mark.asyncio
async def test_reranker_max_document_chars_config_reaches_provider(
    session_maker,
    test_project,
    app_config,
):
    max_chars = 12
    repository = _semantic_search_repository(
        session_maker,
        test_project.id,
        app_config,
        reranker_max_document_chars=max_chars,
    )
    await _index_two_auth_notes(repository)
    reranker = _FakeReranker({"Alpha": 0.1, "Bravo": 0.9})
    repository._rerank_provider = reranker

    await repository.search(
        search_text="auth session token",
        retrieval_mode=SearchRetrievalMode.VECTOR,
        limit=5,
    )

    assert reranker.document_batches == [
        [
            build_rerank_document(
                "Alpha Auth Guide",
                "auth login session token overview",
                max_chars,
            ),
            build_rerank_document(
                "Bravo Auth Guide",
                "auth login session token deep dive",
                max_chars,
            ),
        ]
    ]


@pytest.mark.parametrize(
    ("backend", "expected_type"),
    [
        (DatabaseBackend.SQLITE, SQLiteSearchRepository),
        (DatabaseBackend.POSTGRES, PostgresSearchRepository),
    ],
)
def test_create_search_repository_injects_reranker_for_both_backends(
    monkeypatch,
    backend,
    expected_type,
):
    reranker = _FakeReranker({})
    embedding_provider = _StubEmbeddingProvider()
    vector_index = MagicMock()
    config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/test"},
        default_project="test-project",
        database_backend=backend,
        semantic_search_enabled=True,
    )
    monkeypatch.setattr(
        search_repository_module,
        "create_embedding_provider",
        lambda _config: embedding_provider,
    )
    monkeypatch.setattr(
        search_repository_module,
        "create_semantic_vector_index",
        lambda **_kwargs: (
            "pgvector" if backend == DatabaseBackend.POSTGRES else "sqlite-vec",
            vector_index,
        ),
    )
    monkeypatch.setattr(
        search_repository_module,
        "create_rerank_provider",
        lambda _config: reranker,
    )

    repository = create_search_repository(
        MagicMock(),
        project_id=1,
        app_config=config,
        database_backend=backend,
    )

    assert isinstance(repository, expected_type)
    assert repository._rerank_provider is reranker


@pytest.mark.parametrize(
    ("repository_type", "repository_module", "backend"),
    [
        (
            SQLiteSearchRepository,
            sqlite_search_repository_module,
            DatabaseBackend.SQLITE,
        ),
        (
            PostgresSearchRepository,
            postgres_search_repository_module,
            DatabaseBackend.POSTGRES,
        ),
    ],
)
@pytest.mark.parametrize("semantic_search_enabled", [False, True])
def test_repository_self_resolves_reranker_only_when_semantic_search_is_enabled(
    monkeypatch,
    repository_type,
    repository_module,
    backend,
    semantic_search_enabled,
):
    reranker = _FakeReranker({})
    resolver = MagicMock(return_value=reranker)
    monkeypatch.setattr(repository_module, "create_rerank_provider", resolver)
    config = BasicMemoryConfig(
        env="test",
        projects={"test-project": "/tmp/test"},
        default_project="test-project",
        database_backend=backend,
        semantic_search_enabled=semantic_search_enabled,
    )

    repository = repository_type(
        MagicMock(),
        project_id=1,
        app_config=config,
        embedding_provider=_StubEmbeddingProvider(),
        vector_index=MagicMock(),
    )

    if semantic_search_enabled:
        resolver.assert_called_once_with(config)
        assert repository._rerank_provider is reranker
    else:
        resolver.assert_not_called()
        assert repository._rerank_provider is None
