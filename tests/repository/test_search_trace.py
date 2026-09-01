"""Execution-native search trace builders and repository integration."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import event, text

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_repository_base import FUSION_BONUS
from basic_memory.repository.search_trace import (
    BelowThreshold,
    FilteredOut,
    FinalResultEntry,
    FtsQueryTrace,
    HydrationDropped,
    HydrationDropKey,
    HybridQueryTrace,
    ManifestReadiness,
    MissingSearchRow,
    QueryMeta,
    RetrievalMode,
    RerankerConfigSummary,
    SearchTraceCollector,
    VectorQueryTrace,
    build_fts_page_stage,
    build_fusion_stage,
    build_rerank_stage,
    build_vector_stage,
    classify_hydration_drops,
    finalize_query_trace,
)
from basic_memory.repository.semantic_vector_index import (
    VectorDeletion,
    VectorIndexScope,
    VectorKey,
    VectorMatch,
    VectorRecord,
)
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.schemas.inspect import query_trace_response
from basic_memory.schemas.search import SearchItemType, SearchQuery, SearchRetrievalMode
from basic_memory.services.retrieval_inspect import explain_query
from basic_memory.services.search_service import SearchService

type BackendRepository = SQLiteSearchRepository | PostgresSearchRepository


class _TraceEmbeddingProvider:
    model_name = "trace-embedding"
    dimensions = 4

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, 0.0] for _text in texts]

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {}


class _TraceVectorIndex:
    def __init__(self, project_id: int) -> None:
        self.matches: list[VectorMatch] = []
        self.scope = VectorIndexScope(
            namespace="trace-test",
            project_id=project_id,
            embedding_identity="trace-embedding",
            dimensions=4,
        )

    async def initialize(self) -> None:
        return None

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        return None

    async def delete(self, records: Sequence[VectorDeletion]) -> None:
        return None

    async def delete_entity(self, entity_id: int) -> None:
        return None

    async def search(self, query: Sequence[float], *, limit: int) -> list[VectorMatch]:
        return self.matches[:limit]


class _ReverseReranker:
    model_name = "reverse-reranker"

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        return [0.1, 0.9][: len(documents)]

    def runtime_log_attrs(self) -> dict[str, Any]:
        return {}


def _meta(mode: RetrievalMode) -> QueryMeta:
    return QueryMeta(
        query_text="alpha",
        retrieval_mode=mode,
        limit=2,
        offset=0,
        project_id=1,
        candidate_limit=10,
        rerank_pool_size=0,
        embedding_model="embedding",
        vector_index="index",
        fusion_formula_version="max+0.3*min/v1",
        min_similarity=0.2,
        min_similarity_source="config",
        reranker=RerankerConfigSummary(enabled=False, model=None, candidates=20),
        rerank_applied=False,
        rerank_skipped_reason="disabled",
        total_ms=1.0,
    )


def test_stage_builders_freeze_plain_values_and_exact_fusion_math():
    vector = build_vector_stage(
        candidate_limit=10,
        adapter_match_count=3,
        hydrated_count=2,
        drops=(
            HydrationDropped(
                entity_id=3,
                chunk_key="entity:3:0",
                similarity=0.7,
                reason="pending",
                stored_model="embedding",
                stored_index="index",
            ),
        ),
        effective_min_similarity=0.5,
        min_similarity_source="query",
        threshold_rejections=(BelowThreshold(key=("entity", 2), similarity=0.4, threshold=0.5),),
        filter_rejections=(FilteredOut(key=("entity", 4)),),
        missing_search_rows=(MissingSearchRow(key=("entity", 5)),),
        chunk_matches={
            ("entity", 1): [("entity:1:0", 0.8, 1)],
            ("entity", 2): [("entity:2:0", 0.4, 2)],
        },
        embed_ms=1.25,
        vector_query_ms=2.5,
    )
    assert vector.candidate_limit == 10
    assert vector.chunk_matches[0].chunk_key == "entity:1:0"
    assert vector.threshold_rejections[0].threshold == 0.5

    fts = build_fts_page_stage(
        [(("entity", 1), -5.0), (("entity", 2), -2.5)],
        normalized_scores={("entity", 1): 1.0, ("entity", 2): 0.5},
        entity_ids={("entity", 1): 11, ("entity", 2): 22},
        fts_max_abs=5.0,
        relaxed_fallback_used=True,
        fts_ms=3.0,
    )
    assert fts.relaxed_fallback_used is True
    assert [entry.rank for entry in fts.raw_scores] == [1, 2]
    assert [entry.entity_id for entry in fts.raw_scores] == [11, 22]
    assert [entry.entity_id for entry in fts.normalized_scores or ()] == [11, 22]

    fusion = build_fusion_stage(
        formula_version="max+0.3*min/v1",
        bonus=FUSION_BONUS,
        fts_scores={("entity", 1): 1.0},
        fts_ranks={("entity", 1): 0},
        vector_scores={("entity", 1): 0.8, ("entity", 2): 0.7},
        vector_ranks={("entity", 1): 0, ("entity", 2): 1},
        ranked_scores=[
            (("entity", 1), 1.0 + FUSION_BONUS * 0.8),
            (("entity", 2), 0.7),
        ],
        fusion_ms=0.5,
    )
    assert fusion.entries[0].fused_score == pytest.approx(1.24)
    assert fusion.entries[0].dual_source is True

    rerank = build_rerank_stage(
        provider_model="reranker",
        reranker_candidates=2,
        pre_rerank_scores={("entity", 1): 1.24, ("entity", 2): 0.7},
        pool_keys=[("entity", 1)],
        rerank_scores={("entity", 1): 0.2},
        post_rerank_rows=[(("entity", 1), 0.2), (("entity", 2), 0.1)],
        demoted_scores={("entity", 2): 0.1},
        tail_floor=0.2,
        stable_pool_refetched=True,
        rerank_ms=4.0,
    )
    assert rerank.entries[0].pre_rerank_score == pytest.approx(1.24)
    assert rerank.entries[1].rerank_score is None
    assert rerank.entries[1].demoted_score == pytest.approx(0.1)


def test_fts_degenerate_stage_and_mode_specific_finalize_fail_fast():
    with pytest.raises(ValueError, match="initial vector stage"):
        build_vector_stage()

    degenerate = build_fts_page_stage([], relaxed_fallback_used=True)
    collector = SearchTraceCollector(fts=degenerate)
    trace = finalize_query_trace(collector, _meta("fts"), (), "fts")
    assert isinstance(trace, FtsQueryTrace)
    assert trace.fts.result_count == 0
    assert trace.fts.relaxed_fallback_used is True

    with pytest.raises(ValueError, match="without readiness and vector stages"):
        finalize_query_trace(SearchTraceCollector(), _meta("vector"), (), "vector")
    with pytest.raises(ValueError, match="without readiness, FTS, vector, and fusion"):
        finalize_query_trace(SearchTraceCollector(), _meta("hybrid"), (), "hybrid")
    with pytest.raises(ValueError, match="without an FTS stage"):
        finalize_query_trace(SearchTraceCollector(), _meta("fts"), (), "fts")
    with pytest.raises(ValueError, match="incompatible semantic stages"):
        finalize_query_trace(
            SearchTraceCollector(fts=degenerate, readiness=ManifestReadiness("i", "m", 0, 0, 0)),
            _meta("fts"),
            (),
            "fts",
        )
    with pytest.raises(ValueError, match="incompatible FTS or fusion stages"):
        finalize_query_trace(
            SearchTraceCollector(
                fts=degenerate,
                vector=build_vector_stage(
                    candidate_limit=1,
                    adapter_match_count=0,
                    hydrated_count=0,
                ),
                readiness=ManifestReadiness("i", "m", 0, 0, 0),
            ),
            _meta("vector"),
            (),
            "vector",
        )


def test_query_response_flattens_every_rejection_variant():
    vector = build_vector_stage(
        candidate_limit=10,
        adapter_match_count=7,
        hydrated_count=4,
        drops=(
            HydrationDropped(2, "entity:2:0", 0.8, "not_in_manifest", None, None),
            HydrationDropped(3, "entity:3:0", 0.7, "pending", "m", "i"),
            HydrationDropped(4, "entity:4:0", 0.6, "model_mismatch", "old", "i"),
            HydrationDropped(5, "entity:5:0", 0.5, "index_mismatch", "m", "old"),
            HydrationDropped(10, "entity:10:0", 0.45, "readiness_changed", "m", "i"),
            HydrationDropped(11, "malformed-key", 0.35, "not_in_manifest", None, None),
        ),
        threshold_rejections=(BelowThreshold(("entity", 6), 0.4, 0.5),),
        filter_rejections=(FilteredOut(("entity", 7)),),
        missing_search_rows=(MissingSearchRow(("entity", 8)),),
        chunk_matches={
            ("entity", 1): [("entity:1:0", 0.9, 1)],
            ("entity", 6): [("entity:6:0", 0.4, 6)],
            ("entity", 7): [("entity:7:0", 0.8, 7)],
            ("entity", 8): [("entity:8:0", 0.7, 8)],
            ("entity", 9): [("entity:9:0", 0.65, 9)],
        },
    )
    collector = SearchTraceCollector(
        vector=vector,
        readiness=ManifestReadiness("i", "m", 4, 1, 2),
    )
    final = (FinalResultEntry(("entity", 1), 1, "One", "one", "one.md", 1, 0.9),)
    trace = finalize_query_trace(collector, _meta("vector"), final, "vector")
    response = query_trace_response(
        trace,
        {1: "external-1", 2: "external-2", 6: "external-6", 11: "external-11"},
    )
    assert {candidate.disposition for candidate in response.candidates} == {
        "returned",
        "not_in_manifest",
        "pending",
        "model_mismatch",
        "index_mismatch",
        "readiness_changed",
        "below_threshold",
        "filtered_out",
        "missing_search_row",
        "beyond_page_window",
    }
    surviving = {candidate.id: candidate for candidate in response.candidates}
    assert surviving[1].scores.vector_rank == 1
    assert surviving[1].external_id == "external-1"
    assert surviving[2].external_id == "external-2"
    # A hydrated match rejected by the threshold keeps its owner from the chunk match,
    # so the response can still enrich it with a stable external id.
    assert surviving[6].external_id == "external-6"
    assert surviving[9].scores.vector_rank == 2
    vector_stage = next(stage for stage in response.stages if stage.name == "vector")
    assert vector_stage.count_out == 4
    assert vector_stage.dropped == 3
    assert next(stage for stage in response.stages if stage.name == "row_collapse").dropped is None
    # Threshold (entity 6), filter (entity 7), and missing-row (entity 8) rejections
    # land after the collapse; the plan must show those losses, not jump from 5 to 1.
    row_filters = next(stage for stage in response.stages if stage.name == "row_filters")
    assert (row_filters.count_in, row_filters.count_out, row_filters.dropped) == (5, 2, 3)
    malformed = next(candidate for candidate in response.candidates if candidate.id is None)
    assert malformed.type is None
    assert malformed.external_id == "external-11"
    assert malformed.rejection_detail is not None
    assert malformed.rejection_detail.chunk_key == "malformed-key"
    assert malformed.matched_chunks == []
    assert [chunk.chunk_key for chunk in malformed.dropped_chunks] == ["malformed-key"]


def test_query_response_keeps_row_when_a_ready_chunk_survives_a_dropped_sibling():
    vector = build_vector_stage(
        candidate_limit=10,
        adapter_match_count=4,
        hydrated_count=3,
        drops=(
            HydrationDropped(
                1,
                "entity:1:1",
                0.95,
                "pending",
                "m",
                "i",
            ),
        ),
        chunk_matches={
            ("entity", 1): [("entity:1:0", 0.8, 1), ("entity:1:2", 0.7, 1)],
            ("entity", 2): [("entity:2:0", 0.9, 2)],
        },
    )
    trace = finalize_query_trace(
        SearchTraceCollector(
            vector=vector,
            readiness=ManifestReadiness("i", "m", 2, 1, 0),
        ),
        replace(_meta("vector"), limit=1),
        (FinalResultEntry(("entity", 2), 2, "Two", "two", "two.md", 1, 0.9),),
        "vector",
    )

    response = query_trace_response(trace)
    candidates = {candidate.id: candidate for candidate in response.candidates}
    assert candidates[1].disposition == "beyond_page_window"
    assert candidates[1].scores.vector_similarity == pytest.approx(0.8)
    assert candidates[1].scores.vector_rank == 2
    assert [chunk.chunk_key for chunk in candidates[1].matched_chunks] == [
        "entity:1:0",
        "entity:1:2",
    ]
    assert [(chunk.chunk_key, chunk.reason) for chunk in candidates[1].dropped_chunks] == [
        ("entity:1:1", "pending")
    ]
    vector_stage = next(stage for stage in response.stages if stage.name == "vector")
    assert (vector_stage.count_in, vector_stage.count_out, vector_stage.dropped) == (4, 3, 1)
    row_collapse = next(stage for stage in response.stages if stage.name == "row_collapse")
    assert (row_collapse.count_in, row_collapse.count_out, row_collapse.dropped) == (3, 2, None)
    row_filters = next(stage for stage in response.stages if stage.name == "row_filters")
    assert (row_filters.count_in, row_filters.count_out, row_filters.dropped) == (2, 2, 0)


def test_query_response_uses_best_dropped_chunk_rejection_deterministically():
    vector = build_vector_stage(
        candidate_limit=10,
        adapter_match_count=3,
        hydrated_count=0,
        drops=(
            HydrationDropped(1, "entity:1:2", 0.8, "pending", "m", "i"),
            HydrationDropped(1, "entity:1:1", 0.9, "model_mismatch", "old", "i"),
            HydrationDropped(1, "entity:1:0", 0.9, "index_mismatch", "m", "old"),
        ),
    )
    trace = finalize_query_trace(
        SearchTraceCollector(
            vector=vector,
            readiness=ManifestReadiness("i", "m", 0, 3, 0),
        ),
        _meta("vector"),
        (),
        "vector",
    )

    candidate = query_trace_response(trace).candidates[0]

    assert candidate.disposition == "index_mismatch"
    assert candidate.rejection_detail is not None
    assert candidate.rejection_detail.chunk_key == "entity:1:0"
    assert candidate.scores.vector_similarity == pytest.approx(0.9)
    assert [chunk.chunk_key for chunk in candidate.dropped_chunks] == [
        "entity:1:2",
        "entity:1:1",
        "entity:1:0",
    ]


def test_query_response_clears_vector_rejection_for_fts_fusion_survivor():
    fts = build_fts_page_stage(
        [(("entity", 2), -1.0), (("entity", 1), -0.8)],
        normalized_scores={("entity", 2): 1.0, ("entity", 1): 0.8},
        fts_max_abs=1.0,
        relaxed_fallback_used=False,
    )
    vector = build_vector_stage(
        candidate_limit=10,
        adapter_match_count=1,
        hydrated_count=1,
        threshold_rejections=(BelowThreshold(("entity", 1), 0.1, 0.5),),
        chunk_matches={("entity", 1): [("entity:1:0", 0.1, 1)]},
    )
    fusion = build_fusion_stage(
        formula_version="max+0.3*min/v1",
        bonus=FUSION_BONUS,
        fts_scores={("entity", 2): 1.0, ("entity", 1): 0.8},
        fts_ranks={("entity", 2): 0, ("entity", 1): 1},
        vector_scores={},
        vector_ranks={},
        ranked_scores=[(("entity", 2), 1.0), (("entity", 1), 0.8)],
        fusion_ms=0.1,
    )
    trace = finalize_query_trace(
        SearchTraceCollector(
            fts=fts,
            vector=vector,
            fusion=fusion,
            readiness=ManifestReadiness("i", "m", 1, 0, 0),
        ),
        replace(_meta("hybrid"), limit=1),
        (FinalResultEntry(("entity", 2), 2, "Two", "two", "two.md", 1, 1.0),),
        "hybrid",
    )

    response = query_trace_response(trace)
    candidates = {candidate.id: candidate for candidate in response.candidates}

    # Hybrid awaits FTS before embedding/vector, and the displayed plan follows suit.
    assert [stage.name for stage in response.stages] == [
        "fts",
        "embedding",
        "vector",
        "row_collapse",
        "row_filters",
        "fusion",
    ]
    assert candidates[1].disposition == "beyond_page_window"
    assert candidates[1].rejection_detail is not None
    assert candidates[1].rejection_detail.reason == "beyond_page_window"


def test_query_response_enriches_fts_only_hybrid_miss():
    fts = build_fts_page_stage(
        [(("entity", 1), -1.0), (("entity", 2), -0.8)],
        normalized_scores={("entity", 1): 1.0, ("entity", 2): 0.8},
        entity_ids={("entity", 1): 1, ("entity", 2): 2},
        fts_max_abs=1.0,
        relaxed_fallback_used=False,
    )
    vector = build_vector_stage(candidate_limit=10, adapter_match_count=0, hydrated_count=0)
    fusion = build_fusion_stage(
        formula_version="max+0.3*min/v1",
        bonus=FUSION_BONUS,
        fts_scores={("entity", 1): 1.0, ("entity", 2): 0.8},
        fts_ranks={("entity", 1): 0, ("entity", 2): 1},
        vector_scores={},
        vector_ranks={},
        ranked_scores=[(("entity", 1), 1.0), (("entity", 2), 0.8)],
        fusion_ms=0.1,
    )
    trace = finalize_query_trace(
        SearchTraceCollector(
            fts=fts,
            vector=vector,
            fusion=fusion,
            readiness=ManifestReadiness("i", "m", 0, 0, 0),
        ),
        replace(_meta("hybrid"), limit=1),
        (FinalResultEntry(("entity", 1), 1, "One", "one", "one.md", 1, 1.0),),
        "hybrid",
    )

    response = query_trace_response(trace, {1: "external-1", 2: "external-2"})
    candidates = {candidate.id: candidate for candidate in response.candidates}

    assert candidates[2].disposition == "beyond_page_window"
    assert candidates[2].external_id == "external-2"


def test_fts_query_response_uses_global_ranks_after_sql_offset():
    fts = build_fts_page_stage(
        [(("entity", 11), -1.0), (("entity", 12), -0.8)],
        relaxed_fallback_used=False,
    )
    trace = finalize_query_trace(
        SearchTraceCollector(fts=fts),
        replace(_meta("fts"), limit=2, offset=10),
        (
            FinalResultEntry(("entity", 11), 11, "Eleven", "eleven", "11.md", 11, -1.0),
            FinalResultEntry(("entity", 12), 12, "Twelve", "twelve", "12.md", 12, -0.8),
        ),
        "fts",
    )

    response = query_trace_response(trace)

    assert [candidate.scores.fts_rank for candidate in response.candidates] == [11, 12]
    # FTS execution never reads the vector manifest; zeros would misreport a
    # populated index as empty.
    assert response.engine.ready_rows is None
    assert response.engine.pending_rows is None
    assert response.engine.other_identity_rows is None


def _repository(
    session_maker,
    test_project,
    app_config: BasicMemoryConfig,
) -> tuple[BackendRepository, _TraceVectorIndex]:
    config = app_config.model_copy(
        update={
            "semantic_search_enabled": True,
            "semantic_min_similarity": 0.0,
            "semantic_vector_k": 10,
        }
    )
    vector_index = _TraceVectorIndex(test_project.id)
    repository_type = (
        PostgresSearchRepository
        if config.database_backend == DatabaseBackend.POSTGRES
        else SQLiteSearchRepository
    )
    repository = repository_type(
        session_maker,
        project_id=test_project.id,
        app_config=config,
        embedding_provider=_TraceEmbeddingProvider(),
        vector_index_name="trace-test",
        vector_index=vector_index,
    )
    return repository, vector_index


def _row(project_id: int, row_id: int, title: str, note_type: str) -> SearchIndexRow:
    now = datetime.now(timezone.utc)
    return SearchIndexRow(
        project_id=project_id,
        id=row_id,
        type="entity",
        title=title,
        permalink=f"notes/{row_id}",
        file_path=f"{row_id}.md",
        metadata={"note_type": note_type},
        entity_id=row_id,
        content_stems=f"{title} auth retrieval",
        content_snippet=f"{title} auth retrieval",
        created_at=now,
        updated_at=now,
    )


async def _seed_trace_corpus(
    repository: BackendRepository,
    vector_index: _TraceVectorIndex,
) -> None:
    await repository.init_search_index()
    await repository.bulk_index_items(
        [
            _row(repository.project_id, 1, "Alpha", "keep"),
            _row(repository.project_id, 2, "Bravo", "keep"),
            _row(repository.project_id, 3, "Charlie", "drop"),
        ]
    )
    configured_model = repository._embedding_model_key()
    rows = [
        (1, "entity:1:0", "Alpha auth retrieval", configured_model, "trace-test", "ready"),
        (2, "entity:2:0", "Bravo auth retrieval", configured_model, "trace-test", "ready"),
        (3, "entity:3:0", "Charlie auth retrieval", configured_model, "trace-test", "ready"),
        (999, "entity:999:0", "Missing search row", configured_model, "trace-test", "ready"),
        (4, "entity:4:0", "Pending", configured_model, "trace-test", "pending"),
        (5, "entity:5:0", "Old model", "old-model", "trace-test", "ready"),
        (7, "entity:7:0", "Old index", configured_model, "old-index", "ready"),
    ]
    async with db.scoped_session(repository.session_maker) as session:
        await session.execute(
            text(
                "INSERT INTO search_vector_chunks ("
                "project_id, entity_id, chunk_key, chunk_text, source_hash, "
                "entity_fingerprint, embedding_model, vector_index, embedding_status"
                ") VALUES ("
                ":project_id, :entity_id, :chunk_key, :chunk_text, 'hash', 'fingerprint', "
                ":embedding_model, :vector_index, :embedding_status)"
            ),
            [
                {
                    "project_id": repository.project_id,
                    "entity_id": entity_id,
                    "chunk_key": chunk_key,
                    "chunk_text": chunk_text,
                    "embedding_model": embedding_model,
                    "vector_index": stored_index,
                    "embedding_status": status,
                }
                for entity_id, chunk_key, chunk_text, embedding_model, stored_index, status in rows
            ],
        )
        await session.commit()

    vector_index.matches = [
        VectorMatch(VectorKey(1, "entity:1:0"), 0.95),
        VectorMatch(VectorKey(2, "entity:2:0"), 0.45),
        VectorMatch(VectorKey(3, "entity:3:0"), 0.85),
        VectorMatch(VectorKey(999, "entity:999:0"), 0.8),
        VectorMatch(VectorKey(4, "entity:4:0"), 0.75),
        VectorMatch(VectorKey(5, "entity:5:0"), 0.7),
        VectorMatch(VectorKey(6, "entity:6:0"), 0.65),
        VectorMatch(VectorKey(7, "entity:7:0"), 0.6),
    ]


@pytest.mark.asyncio
async def test_vector_trace_captures_drops_threshold_filter_and_missing_on_same_execution(
    session_maker,
    test_project,
    app_config,
    engine_factory,
):
    repository, vector_index = _repository(session_maker, test_project, app_config)
    await _seed_trace_corpus(repository, vector_index)

    statements: list[str] = []
    engine, _session_maker = engine_factory

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        without_trace = await repository.search(
            search_text="auth",
            note_types=["keep"],
            retrieval_mode=SearchRetrievalMode.VECTOR,
            min_similarity=0.5,
            limit=10,
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record_statement)
    assert not any("GROUP BY embedding_status" in statement for statement in statements)
    assert not any(
        "embedding_model, vector_index, embedding_status" in statement for statement in statements
    )

    collector = SearchTraceCollector()
    with_trace = await repository.search(
        search_text="auth",
        note_types=["keep"],
        retrieval_mode=SearchRetrievalMode.VECTOR,
        min_similarity=0.5,
        limit=10,
        trace=collector,
    )
    assert [(row.type, row.id, row.score) for row in with_trace] == [
        (row.type, row.id, row.score) for row in without_trace
    ]
    assert [row.id for row in with_trace] == [1]
    assert collector.readiness == ManifestReadiness(
        configured_index="trace-test",
        configured_model=repository._embedding_model_key(),
        ready_rows=4,
        pending_rows=1,
        other_identity_rows=2,
    )
    assert collector.vector is not None
    assert {drop.reason for drop in collector.vector.drops} == {
        "pending",
        "model_mismatch",
        "not_in_manifest",
        "index_mismatch",
    }
    assert [rejection.key for rejection in collector.vector.threshold_rejections] == [("entity", 2)]
    assert [rejection.key for rejection in collector.vector.filter_rejections] == [("entity", 3)]
    assert [rejection.key for rejection in collector.vector.missing_search_rows] == [
        ("entity", 999)
    ]
    assert all("auth retrieval" not in match.chunk_key for match in collector.vector.chunk_matches)

    async with db.scoped_session(repository.session_maker) as session:
        assert await classify_hydration_drops(session, repository.project_id, ()) == ()
        readiness_race = await classify_hydration_drops(
            session,
            repository.project_id,
            (
                HydrationDropKey(
                    entity_id=1,
                    chunk_key="entity:1:0",
                    similarity=0.95,
                    configured_index="trace-test",
                    configured_model=repository._embedding_model_key(),
                ),
            ),
        )
    assert readiness_race[0].reason == "readiness_changed"
    assert readiness_race[0].entity_id == 1


@pytest.mark.asyncio
async def test_classify_hydration_drops_batches_large_unhealthy_candidate_set(
    session_maker,
    test_project,
):
    dropped_keys = tuple(
        HydrationDropKey(
            entity_id=entity_id,
            chunk_key=f"entity:{entity_id}:0",
            similarity=0.5,
            configured_index="trace-test",
            configured_model="trace-embedding",
        )
        for entity_id in range(10_000, 11_001)
    )

    async with db.scoped_session(session_maker) as session:
        classified = await classify_hydration_drops(
            session,
            test_project.id,
            dropped_keys,
        )

    assert len(classified) == len(dropped_keys)
    assert {drop.reason for drop in classified} == {"not_in_manifest"}
    assert [classified[0].entity_id, classified[-1].entity_id] == [10_000, 11_000]


@pytest.mark.asyncio
async def test_classify_hydration_drop_observes_pending_to_ready_transition(
    session_maker,
    test_project,
    app_config,
):
    repository, vector_index = _repository(session_maker, test_project, app_config)
    await _seed_trace_corpus(repository, vector_index)
    match = VectorMatch(VectorKey(1, "entity:1:0"), 0.95)
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "UPDATE search_vector_chunks SET embedding_status = 'pending' "
                "WHERE project_id = :project_id AND entity_id = 1"
            ),
            {"project_id": test_project.id},
        )
        await session.commit()
    async with db.scoped_session(session_maker) as session:
        assert await repository._hydrate_vector_matches(session, [match]) == []

    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "UPDATE search_vector_chunks SET embedding_status = 'ready' "
                "WHERE project_id = :project_id AND entity_id = 1"
            ),
            {"project_id": test_project.id},
        )
        await session.commit()
    async with db.scoped_session(session_maker) as session:
        classified = await classify_hydration_drops(
            session,
            test_project.id,
            (
                HydrationDropKey(
                    entity_id=1,
                    chunk_key="entity:1:0",
                    similarity=0.95,
                    configured_index="trace-test",
                    configured_model=repository._embedding_model_key(),
                ),
            ),
        )

    assert classified[0].reason == "readiness_changed"


@pytest.mark.asyncio
async def test_fts_vector_hybrid_and_rerank_trace_variants(
    session_maker,
    test_project,
    app_config,
):
    repository, vector_index = _repository(session_maker, test_project, app_config)
    await _seed_trace_corpus(repository, vector_index)
    search_service = SearchService(repository, MagicMock(), MagicMock(), session_maker)

    fts_trace = await explain_query(
        search_service,
        SearchQuery(text="Alpha", retrieval_mode=SearchRetrievalMode.FTS),
        limit=10,
        offset=0,
    )
    assert isinstance(fts_trace, FtsQueryTrace)
    assert fts_trace.fts.normalized_scores is None
    assert fts_trace.fts.fts_ms is not None
    assert fts_trace.fts.fts_ms >= 0.0
    assert fts_trace.final[0].key == ("entity", 1)

    relaxed_fts_trace = await explain_query(
        search_service,
        SearchQuery(text="Alpha absent-term", retrieval_mode=SearchRetrievalMode.FTS),
        limit=10,
        offset=0,
    )
    assert isinstance(relaxed_fts_trace, FtsQueryTrace)
    assert relaxed_fts_trace.fts.relaxed_fallback_used is True
    assert relaxed_fts_trace.fts.fts_ms is not None
    assert relaxed_fts_trace.fts.fts_ms >= 0.0

    vector_trace = await explain_query(
        search_service,
        SearchQuery(text="auth", retrieval_mode=SearchRetrievalMode.VECTOR),
        limit=2,
        offset=0,
    )
    assert isinstance(vector_trace, VectorQueryTrace)
    assert vector_trace.vector.adapter_match_count == len(vector_index.matches)

    hybrid_trace = await explain_query(
        search_service,
        SearchQuery(text="Alpha", retrieval_mode=SearchRetrievalMode.HYBRID),
        limit=3,
        offset=0,
    )
    assert isinstance(hybrid_trace, HybridQueryTrace)
    alpha_fts = next(score for score in hybrid_trace.fts.raw_scores if score.key == ("entity", 1))
    assert alpha_fts.entity_id == 1
    alpha = next(entry for entry in hybrid_trace.fusion.entries if entry.key == ("entity", 1))
    assert alpha.fts_score == pytest.approx(1.0)
    assert alpha.vector_score == pytest.approx(0.95)
    assert alpha.fused_score == pytest.approx(1.0 + FUSION_BONUS * 0.95)
    assert alpha.dual_source is True

    repository._rerank_provider = _ReverseReranker()
    repository._semantic_vector_k = 3
    repository._reranker_candidates = 2
    reranked_trace = await explain_query(
        search_service,
        SearchQuery(text="auth", retrieval_mode=SearchRetrievalMode.VECTOR),
        limit=3,
        offset=0,
    )
    assert isinstance(reranked_trace, VectorQueryTrace)
    assert reranked_trace.rerank is not None
    assert reranked_trace.rerank.stable_pool_refetched is True
    assert reranked_trace.rerank.entries[0].key == ("entity", 3)
    alpha_rerank = next(
        entry for entry in reranked_trace.rerank.entries if entry.key == ("entity", 1)
    )
    assert alpha_rerank.pre_rerank_rank == 1
    assert alpha_rerank.pre_rerank_score == pytest.approx(0.95)
    assert alpha_rerank.post_rerank_rank == 2
    tail = reranked_trace.rerank.entries[-1]
    assert tail.rerank_score is None
    assert tail.demoted_score is not None
    assert tail.demoted_score < reranked_trace.rerank.tail_floor


@pytest.mark.asyncio
async def test_external_overfetch_trace_trims_to_the_candidate_window(
    session_maker,
    test_project,
    app_config,
):
    """Chunks hydrated beyond candidate_limit never ran; the trace must not invent them."""
    repository, vector_index = _repository(session_maker, test_project, app_config)
    await repository.init_search_index()
    live_ids = list(range(1, 21))
    await repository.bulk_index_items(
        [
            _row(repository.project_id, entity_id, f"Note {entity_id}", "keep")
            for entity_id in live_ids
        ]
    )
    configured_model = repository._embedding_model_key()
    async with db.scoped_session(repository.session_maker) as session:
        await session.execute(
            text(
                "INSERT INTO search_vector_chunks ("
                "project_id, entity_id, chunk_key, chunk_text, source_hash, "
                "entity_fingerprint, embedding_model, vector_index, embedding_status"
                ") VALUES ("
                ":project_id, :entity_id, :chunk_key, :chunk_text, 'hash', 'fingerprint', "
                ":embedding_model, 'trace-test', 'ready')"
            ),
            [
                {
                    "project_id": repository.project_id,
                    "entity_id": entity_id,
                    "chunk_key": f"entity:{entity_id}:0",
                    "chunk_text": f"Note {entity_id} auth retrieval",
                    "embedding_model": configured_model,
                }
                for entity_id in live_ids
            ],
        )
        await session.commit()

    # A second owner sharing entity 1's parseable chunk key — permitted storage shape
    # since manifest uniqueness includes entity_id. It hydrates beyond the window and
    # must be trimmed even though the key string matches an in-window chunk.
    async with db.scoped_session(repository.session_maker) as session:
        await session.execute(
            text(
                "INSERT INTO search_vector_chunks ("
                "project_id, entity_id, chunk_key, chunk_text, source_hash, "
                "entity_fingerprint, embedding_model, vector_index, embedding_status"
                ") VALUES ("
                ":project_id, 999, 'entity:1:0', 'Stale twin', 'hash', 'fingerprint', "
                ":embedding_model, 'trace-test', 'ready')"
            ),
            {"project_id": repository.project_id, "embedding_model": configured_model},
        )
        await session.commit()

    # First scan (limit 10): five stale hits crowd out live rows, forcing the
    # geometric rescan; the second scan hydrates 14 live chunks plus the foreign-owner
    # twin for a 10-wide window.
    stale = [
        VectorMatch(VectorKey(100 + offset, f"entity:{100 + offset}:0"), 0.99 - offset * 0.001)
        for offset in range(5)
    ]
    live = [
        VectorMatch(VectorKey(entity_id, f"entity:{entity_id}:0"), 0.9 - entity_id * 0.001)
        for entity_id in live_ids
    ]
    vector_index.matches = stale + live[:14] + [VectorMatch(VectorKey(999, "entity:1:0"), 0.5)]

    collector = SearchTraceCollector()
    results = await repository.search(
        search_text="auth",
        retrieval_mode=SearchRetrievalMode.VECTOR,
        min_similarity=0.0,
        limit=1,
        trace=collector,
    )

    assert len(results) == 1
    assert collector.vector is not None
    assert collector.vector.adapter_match_count == 20
    # Hydration accounting keeps full-scan scope (15 ready chunks, 5 stale drops);
    # only the served chunk matches are trimmed to the 10-wide candidate window.
    assert collector.vector.hydrated_count == 15
    assert len(collector.vector.drops) == 5
    served = {chunk_match.key for chunk_match in collector.vector.chunk_matches}
    assert served == {("entity", entity_id) for entity_id in live_ids[:10]}
    assert {drop.reason for drop in collector.vector.drops} == {"not_in_manifest"}

    # The foreign-owner twin hydrated beyond the window and must not survive the trim
    # under entity 1's identity.
    assert all(chunk_match.entity_id != 999 for chunk_match in collector.vector.chunk_matches)

    trace = finalize_query_trace(
        collector,
        replace(_meta("vector"), limit=1),
        (
            FinalResultEntry(
                ("entity", live_ids[0]),
                live_ids[0],
                f"Note {live_ids[0]}",
                f"notes/{live_ids[0]}",
                f"{live_ids[0]}.md",
                1,
                0.9,
            ),
        ),
        "vector",
    )
    response = query_trace_response(trace)
    # The truncation is a stage of its own: vector 20→15 (5 hydration drops), then
    # candidate_window 15→10, so no count ever mixes scopes.
    vector_stage = next(stage for stage in response.stages if stage.name == "vector")
    assert (vector_stage.count_in, vector_stage.count_out, vector_stage.dropped) == (20, 15, 5)
    window = next(stage for stage in response.stages if stage.name == "candidate_window")
    assert (window.count_in, window.count_out, window.dropped) == (15, 10, 5)
    row_collapse = next(stage for stage in response.stages if stage.name == "row_collapse")
    assert row_collapse.count_in == 10


@pytest.mark.asyncio
async def test_hybrid_trace_drops_vector_rows_cut_before_fusion(
    session_maker,
    test_project,
    app_config,
):
    """Rows the vector leg hydrates but never hands to fusion must not read as returned."""
    repository, vector_index = _repository(session_maker, test_project, app_config)
    await repository.init_search_index()
    live_ids = list(range(1, 21))
    await repository.bulk_index_items(
        [
            _row(repository.project_id, entity_id, f"Note {entity_id}", "keep")
            for entity_id in live_ids
        ]
    )
    configured_model = repository._embedding_model_key()
    async with db.scoped_session(repository.session_maker) as session:
        await session.execute(
            text(
                "INSERT INTO search_vector_chunks ("
                "project_id, entity_id, chunk_key, chunk_text, source_hash, "
                "entity_fingerprint, embedding_model, vector_index, embedding_status"
                ") VALUES ("
                ":project_id, :entity_id, :chunk_key, :chunk_text, 'hash', 'fingerprint', "
                ":embedding_model, 'trace-test', 'ready')"
            ),
            [
                {
                    "project_id": repository.project_id,
                    "entity_id": entity_id,
                    "chunk_key": f"entity:{entity_id}:0",
                    "chunk_text": f"Note {entity_id} auth retrieval",
                    "embedding_model": configured_model,
                }
                for entity_id in live_ids
            ],
        )
        await session.commit()
    vector_index.matches = [
        VectorMatch(VectorKey(entity_id, f"entity:{entity_id}:0"), 0.9 - entity_id * 0.001)
        for entity_id in live_ids
    ]

    collector = SearchTraceCollector()
    results = await repository.search(
        search_text="auth",
        retrieval_mode=SearchRetrievalMode.HYBRID,
        min_similarity=0.0,
        limit=1,
        trace=collector,
    )
    trace = finalize_query_trace(
        collector,
        replace(_meta("hybrid"), limit=1),
        tuple(
            FinalResultEntry(
                (row.type, row.id),
                row.entity_id,
                row.title,
                row.permalink,
                row.file_path,
                rank,
                row.score or 0.0,
            )
            for rank, row in enumerate(results, start=1)
        ),
        "hybrid",
    )

    response = query_trace_response(trace)

    # Every "returned" candidate must correspond to an actual final result; rows the
    # vector leg hydrated but cut before fusion may not masquerade as returned.
    returned = [c for c in response.candidates if c.disposition == "returned"]
    assert len(returned) == len(results) == 1
    assert collector.vector is not None
    traced_vector_rows = {chunk_match.key for chunk_match in collector.vector.chunk_matches}
    assert collector.fusion is not None
    fused_keys = {entry.key for entry in collector.fusion.entries}
    assert traced_vector_rows <= fused_keys


@pytest.mark.asyncio
async def test_hydrated_chunk_with_malformed_key_is_an_explicit_drop(
    session_maker,
    test_project,
    app_config,
    engine_factory,
):
    """A ready chunk with an unparseable key must be named in the trace, not vanish."""
    repository, vector_index = _repository(session_maker, test_project, app_config)
    await _seed_trace_corpus(repository, vector_index)
    configured_model = repository._embedding_model_key()
    async with db.scoped_session(repository.session_maker) as session:
        await session.execute(
            text(
                "INSERT INTO search_vector_chunks ("
                "project_id, entity_id, chunk_key, chunk_text, source_hash, "
                "entity_fingerprint, embedding_model, vector_index, embedding_status"
                ") VALUES ("
                ":project_id, 1, 'orphan-key-without-shape', 'Orphan text', 'hash', "
                "'fingerprint', :embedding_model, 'trace-test', 'ready')"
            ),
            {"project_id": repository.project_id, "embedding_model": configured_model},
        )
    vector_index.matches = [
        VectorMatch(VectorKey(1, "entity:1:0"), 0.95),
        VectorMatch(VectorKey(1, "orphan-key-without-shape"), 0.9),
    ]

    collector = SearchTraceCollector()
    results = await repository.search(
        search_text="auth",
        note_types=["keep"],
        retrieval_mode=SearchRetrievalMode.VECTOR,
        min_similarity=0.5,
        limit=10,
        trace=collector,
    )

    assert [(row.type, row.id) for row in results] == [("entity", 1)]
    assert collector.vector is not None
    malformed = [drop for drop in collector.vector.drops if drop.reason == "malformed_key"]
    assert [(drop.chunk_key, drop.similarity) for drop in malformed] == [
        ("orphan-key-without-shape", 0.9)
    ]
    assert all(
        chunk_match.chunk_key != "orphan-key-without-shape"
        for chunk_match in collector.vector.chunk_matches
    )
    # Malformed hits are drops, not output: in=2 adapter matches, out=1 served chunk.
    assert collector.vector.adapter_match_count == 2
    assert collector.vector.hydrated_count == 1


@pytest.mark.asyncio
async def test_explain_query_without_criteria_fails_fast(
    session_maker,
    test_project,
    app_config,
):
    """An unsatisfiable query never executes, so explain refuses to invent a trace."""
    repository, _vector_index = _repository(session_maker, test_project, app_config)
    search_service = SearchService(repository, MagicMock(), MagicMock(), session_maker)

    with pytest.raises(ValueError, match="without an FTS stage"):
        await explain_query(search_service, SearchQuery(), limit=10, offset=0)


@pytest.mark.asyncio
async def test_empty_page_distinguishes_pagination_from_empty_candidates(
    session_maker,
    test_project,
    app_config,
):
    """An offset past the ranked rows is page_out_of_range, not no_candidates."""
    repository, vector_index = _repository(session_maker, test_project, app_config)
    await _seed_trace_corpus(repository, vector_index)
    repository._rerank_provider = _ReverseReranker()
    search_service = SearchService(repository, MagicMock(), MagicMock(), session_maker)

    beyond_page = await explain_query(
        search_service,
        SearchQuery(text="auth", retrieval_mode=SearchRetrievalMode.VECTOR),
        limit=3,
        offset=50,
    )
    assert beyond_page.final == ()
    assert beyond_page.meta.rerank_applied is False
    assert beyond_page.meta.rerank_skipped_reason == "page_out_of_range"

    # Hydrated chunks whose rows all fall to the threshold are an empty ranked set:
    # offset 0 cannot be "out of range", whatever the hydration count says.
    all_rejected = await explain_query(
        search_service,
        SearchQuery(
            text="auth",
            retrieval_mode=SearchRetrievalMode.VECTOR,
            min_similarity=0.99,
        ),
        limit=3,
        offset=0,
    )
    assert all_rejected.final == ()
    assert all_rejected.meta.rerank_skipped_reason == "no_candidates"

    vector_index.matches = []
    empty_retrieval = await explain_query(
        search_service,
        SearchQuery(text="auth", retrieval_mode=SearchRetrievalMode.VECTOR),
        limit=3,
        offset=0,
    )
    assert empty_retrieval.final == ()
    assert empty_retrieval.meta.rerank_skipped_reason == "no_candidates"


def test_negative_similarities_survive_without_zero_clamping():
    """With no threshold, negatively correlated matches keep their real scores."""
    vector = build_vector_stage(
        candidate_limit=10,
        adapter_match_count=3,
        hydrated_count=2,
        drops=(HydrationDropped(11, "malformed-key", -0.2, "not_in_manifest", None, None),),
        chunk_matches={
            ("entity", 1): [("entity:1:0", -0.5, 1), ("entity:1:1", -0.4, 1)],
        },
        effective_min_similarity=0.0,
    )
    collector = SearchTraceCollector(
        vector=vector,
        readiness=ManifestReadiness("i", "m", 2, 0, 0),
    )
    final = (FinalResultEntry(("entity", 1), 1, "One", "one", "one.md", 1, -0.4),)
    trace = finalize_query_trace(collector, _meta("vector"), final, "vector")

    response = query_trace_response(trace)

    by_id = {candidate.id: candidate for candidate in response.candidates}
    assert by_id[1].scores.vector_similarity == pytest.approx(-0.4)
    malformed = next(candidate for candidate in response.candidates if candidate.id is None)
    assert malformed.scores.vector_similarity == pytest.approx(-0.2)


def test_relaxed_fts_fallback_is_serialized_on_the_stage():
    """Results produced by the relaxed retry must not read as strict-query matches."""
    relaxed = build_fts_page_stage(
        [(("entity", 1), -1.0)],
        relaxed_fallback_used=True,
    )
    trace = finalize_query_trace(
        SearchTraceCollector(fts=relaxed),
        _meta("fts"),
        (FinalResultEntry(("entity", 1), 1, "One", "one", "one.md", 1, -1.0),),
        "fts",
    )

    response = query_trace_response(trace)

    fts_stage = next(stage for stage in response.stages if stage.name == "fts")
    assert fts_stage.relaxed_fallback_used is True


def test_parseable_drop_with_foreign_owner_stays_a_distinct_candidate():
    """A stale hit under a reused row id must not merge with the live row's evidence."""
    vector = build_vector_stage(
        candidate_limit=10,
        adapter_match_count=3,
        hydrated_count=1,
        drops=(
            # Live owner's sibling chunk drop merges with the live candidate as before.
            HydrationDropped(5, "entity:5:1", 0.6, "pending", "m", "i"),
            # Foreign owner: same parseable chunk key, different entity — a stale
            # adapter hit surviving a search-row ID reuse.
            HydrationDropped(99, "entity:5:0", 0.55, "not_in_manifest", None, None),
        ),
        chunk_matches={("entity", 5): [("entity:5:0", 0.9, 5)]},
    )
    collector = SearchTraceCollector(
        vector=vector,
        readiness=ManifestReadiness("i", "m", 1, 1, 0),
    )
    final = (FinalResultEntry(("entity", 5), 5, "Five", "five", "five.md", 1, 0.9),)
    trace = finalize_query_trace(collector, _meta("vector"), final, "vector")

    response = query_trace_response(trace, {5: "external-5", 99: "external-99"})

    returned = [c for c in response.candidates if c.disposition == "returned"]
    assert [(c.id, c.external_id) for c in returned] == [(5, "external-5")]
    live = returned[0]
    # The live row keeps only its own evidence: one hydrated chunk + its own sibling drop.
    assert [chunk.chunk_key for chunk in live.matched_chunks] == ["entity:5:0"]
    assert [chunk.chunk_key for chunk in live.dropped_chunks] == ["entity:5:1"]
    foreign = next(c for c in response.candidates if c.external_id == "external-99")
    assert foreign.disposition == "not_in_manifest"
    assert foreign.scores.vector_similarity == pytest.approx(0.55)
    assert [chunk.chunk_key for chunk in foreign.dropped_chunks] == ["entity:5:0"]


def test_foreign_owner_drop_stays_split_from_fts_returned_row():
    """A stale same-key drop must not attach to a row returned via FTS alone."""
    fts = build_fts_page_stage(
        [(("entity", 5), -1.0)],
        normalized_scores={("entity", 5): 1.0},
        fts_max_abs=1.0,
        relaxed_fallback_used=False,
    )
    vector = build_vector_stage(
        candidate_limit=10,
        adapter_match_count=1,
        hydrated_count=0,
        drops=(HydrationDropped(99, "entity:5:0", 0.5, "not_in_manifest", None, None),),
    )
    fusion = build_fusion_stage(
        formula_version="max+0.3*min/v1",
        bonus=FUSION_BONUS,
        fts_scores={("entity", 5): 1.0},
        fts_ranks={("entity", 5): 0},
        vector_scores={},
        vector_ranks={},
        ranked_scores=[(("entity", 5), 1.0)],
        fusion_ms=0.1,
    )
    trace = finalize_query_trace(
        SearchTraceCollector(
            fts=fts,
            vector=vector,
            fusion=fusion,
            readiness=ManifestReadiness("i", "m", 0, 0, 1),
        ),
        _meta("hybrid"),
        (FinalResultEntry(("entity", 5), 5, "Five", "five", "five.md", 1, 1.0),),
        "hybrid",
    )

    response = query_trace_response(trace, {5: "external-5", 99: "external-99"})

    returned = [c for c in response.candidates if c.disposition == "returned"]
    assert [(c.id, c.external_id) for c in returned] == [(5, "external-5")]
    assert returned[0].dropped_chunks == []
    foreign = next(c for c in response.candidates if c.external_id == "external-99")
    assert foreign.disposition == "not_in_manifest"
    assert [chunk.chunk_key for chunk in foreign.dropped_chunks] == ["entity:5:0"]


def test_identically_malformed_keys_from_two_entities_stay_distinct_candidates():
    """Two owners serving the same malformed key must not merge their drop evidence."""
    vector = build_vector_stage(
        candidate_limit=10,
        adapter_match_count=3,
        hydrated_count=1,
        drops=(
            HydrationDropped(11, "malformed-key", 0.35, "not_in_manifest", None, None),
            HydrationDropped(12, "malformed-key", 0.30, "not_in_manifest", None, None),
        ),
        chunk_matches={("entity", 1): [("entity:1:0", 0.9, 1)]},
    )
    collector = SearchTraceCollector(
        vector=vector,
        readiness=ManifestReadiness("i", "m", 1, 0, 0),
    )
    final = (FinalResultEntry(("entity", 1), 1, "One", "one", "one.md", 1, 0.9),)
    trace = finalize_query_trace(collector, _meta("vector"), final, "vector")

    response = query_trace_response(trace, {11: "external-11", 12: "external-12"})

    malformed = sorted(
        (c for c in response.candidates if c.id is None),
        key=lambda candidate: candidate.external_id or "",
    )
    assert [
        (c.external_id, [chunk.chunk_key for chunk in c.dropped_chunks]) for c in malformed
    ] == [
        ("external-11", ["malformed-key"]),
        ("external-12", ["malformed-key"]),
    ]
    assert {c.external_id: c.scores.vector_similarity for c in malformed} == {
        "external-11": pytest.approx(0.35),
        "external-12": pytest.approx(0.30),
    }


def test_duplicate_final_rows_keep_one_returned_candidate_per_occurrence():
    """SQLite FTS duplicates keep per-occurrence stage ranks and final ranks."""
    duplicated_page = build_fts_page_stage(
        [(("entity", 1), -1.0), (("entity", 1), -1.0), (("entity", 2), -0.5)],
        relaxed_fallback_used=False,
    )
    # The stage freezes the executed page occurrence-by-occurrence: collapsing it
    # would undercount the page and shift the later unique row's source rank.
    assert duplicated_page.result_count == 3
    assert [(score.key, score.rank) for score in duplicated_page.raw_scores] == [
        (("entity", 1), 1),
        (("entity", 1), 2),
        (("entity", 2), 3),
    ]

    collector = SearchTraceCollector(fts=duplicated_page)
    final = (
        FinalResultEntry(("entity", 1), 1, "One", "one", "one.md", 1, 0.9),
        FinalResultEntry(("entity", 1), 1, "One", "one", "one.md", 2, 0.9),
        FinalResultEntry(("entity", 2), 2, "Two", "two", "two.md", 3, 0.4),
    )
    trace = finalize_query_trace(collector, _meta("fts"), final, "fts")

    response = query_trace_response(trace, {1: "external-1", 2: "external-2"})

    returned = [c for c in response.candidates if c.disposition == "returned"]
    assert [(c.id, c.scores.final_rank) for c in returned] == [(1, 1), (1, 2), (2, 3)]
    # The logical candidate keeps its best (first-occurrence) source rank, and the
    # later unique row keeps the rank of its actual page position.
    assert [c.scores.fts_rank for c in returned] == [1, 1, 3]


def test_non_text_criteria_and_null_owner_rows_stay_inspectable():
    """Prepared criteria render their executed form; NULL-owner rows degrade, not reject."""
    from basic_memory.services.search_service import PreparedSearchQuery, describe_search_criteria

    empty = PreparedSearchQuery(
        search_text=None,
        permalink=None,
        permalink_match=None,
        title=None,
        note_types=None,
        search_item_types=None,
        categories=None,
        after_date=None,
        metadata_filters=None,
        file_path_prefix=None,
        retrieval_mode=SearchRetrievalMode.FTS,
        min_similarity=None,
    )

    described = describe_search_criteria(replace(empty, title="Search Spec", note_types=["note"]))
    assert described == "title=\"Search Spec\" note_types=['note']"

    # SearchItemType's str() is "SearchItemType.ENTITY"; the executed filter is "entity".
    typed = describe_search_criteria(
        replace(empty, search_item_types=[SearchItemType.ENTITY, SearchItemType.OBSERVATION])
    )
    assert typed == "entity_types=['entity', 'observation']"

    # tags=/status= shorthand folds into metadata_filters during prepare_query, so the
    # description shows the executed filters rather than the raw request fields.
    filter_only = describe_search_criteria(
        replace(
            empty,
            categories=["decision"],
            metadata_filters={"tags": ["auth"], "status": "active", "priority": "high"},
        )
    )
    assert filter_only == (
        "categories=['decision'] "
        "metadata_filters={'tags': ['auth'], 'status': 'active', 'priority': 'high'}"
    )

    degenerate = build_fts_page_stage([(("entity", 7), -1.0)], relaxed_fallback_used=False)
    collector = SearchTraceCollector(fts=degenerate)
    final = (FinalResultEntry(("entity", 7), None, "Legacy", None, "legacy.md", 1, 0.5),)
    trace = finalize_query_trace(collector, _meta("fts"), final, "fts")

    response = query_trace_response(trace, {})

    returned = [c for c in response.candidates if c.disposition == "returned"]
    assert [(c.id, c.external_id) for c in returned] == [(7, None)]
