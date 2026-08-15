"""Typed, execution-native trace values for the search retrieval pipeline."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

type TraceKey = tuple[str, int]
type RetrievalMode = Literal["fts", "vector", "hybrid"]
type DropReason = Literal[
    "not_in_manifest",
    "pending",
    "model_mismatch",
    "index_mismatch",
    "readiness_changed",
    "malformed_key",
]
type MinSimilaritySource = Literal["query", "config"]

HYDRATION_DROP_CLASSIFICATION_BATCH_SIZE = 250


# --- Rejections ---


@dataclass(frozen=True, slots=True)
class BelowThreshold:
    key: TraceKey
    similarity: float
    threshold: float


@dataclass(frozen=True, slots=True)
class HydrationDropped:
    entity_id: int
    chunk_key: str
    similarity: float
    reason: DropReason
    stored_model: str | None
    stored_index: str | None


@dataclass(frozen=True, slots=True)
class FilteredOut:
    key: TraceKey


@dataclass(frozen=True, slots=True)
class MissingSearchRow:
    key: TraceKey


@dataclass(frozen=True, slots=True)
class BeyondPageWindow:
    key: TraceKey
    rank: int
    score: float


type Rejection = (
    BelowThreshold | HydrationDropped | FilteredOut | MissingSearchRow | BeyondPageWindow
)


# --- Frozen retrieval stages ---


@dataclass(frozen=True, slots=True)
class VectorChunkMatch:
    key: TraceKey
    chunk_key: str
    similarity: float
    # Owner entity of the search row this chunk serves — known at hydration and kept
    # so rejected candidates can still be enriched with their stable external id.
    entity_id: int | None


@dataclass(frozen=True, slots=True)
class VectorStageTrace:
    candidate_limit: int
    adapter_match_count: int
    hydrated_count: int
    drops: tuple[HydrationDropped, ...]
    effective_min_similarity: float
    min_similarity_source: MinSimilaritySource
    threshold_rejections: tuple[BelowThreshold, ...]
    filter_rejections: tuple[FilteredOut, ...]
    missing_search_rows: tuple[MissingSearchRow, ...]
    chunk_matches: tuple[VectorChunkMatch, ...]
    embed_ms: float
    vector_query_ms: float


@dataclass(frozen=True, slots=True)
class FtsScore:
    key: TraceKey
    score: float
    rank: int
    entity_id: int | None = None


@dataclass(frozen=True, slots=True)
class FtsStageTrace:
    raw_scores: tuple[FtsScore, ...]
    normalized_scores: tuple[FtsScore, ...] | None
    fts_max_abs: float | None
    result_count: int
    relaxed_fallback_used: bool
    fts_ms: float | None


@dataclass(frozen=True, slots=True)
class FusionEntry:
    key: TraceKey
    fts_score: float | None
    fts_rank: int | None
    vector_score: float | None
    vector_rank: int | None
    fused_score: float
    fused_rank: int
    dual_source: bool


@dataclass(frozen=True, slots=True)
class FusionStageTrace:
    formula_version: str
    bonus: float
    entries: tuple[FusionEntry, ...]
    fusion_ms: float


@dataclass(frozen=True, slots=True)
class RerankEntry:
    key: TraceKey
    pre_rerank_rank: int
    pre_rerank_score: float
    rerank_score: float | None
    post_rerank_rank: int
    demoted_score: float | None


@dataclass(frozen=True, slots=True)
class RerankStageTrace:
    provider_model: str
    pool_size: int
    reranker_candidates: int
    entries: tuple[RerankEntry, ...]
    tail_floor: float
    stable_pool_refetched: bool
    rerank_ms: float


@dataclass(frozen=True, slots=True)
class ManifestReadiness:
    configured_index: str
    configured_model: str
    ready_rows: int
    pending_rows: int
    other_identity_rows: int


@dataclass(frozen=True, slots=True)
class FinalResultEntry:
    key: TraceKey
    entity_id: int | None
    title: str | None
    permalink: str | None
    file_path: str
    final_rank: int
    final_score: float


@dataclass(frozen=True, slots=True)
class RerankerConfigSummary:
    enabled: bool
    model: str | None
    candidates: int


@dataclass(frozen=True, slots=True)
class QueryMeta:
    query_text: str
    retrieval_mode: RetrievalMode
    limit: int
    offset: int
    project_id: int
    candidate_limit: int
    rerank_pool_size: int
    embedding_model: str
    vector_index: str
    fusion_formula_version: str
    min_similarity: float
    min_similarity_source: MinSimilaritySource
    reranker: RerankerConfigSummary
    rerank_applied: bool
    rerank_skipped_reason: str | None
    total_ms: float


@dataclass(frozen=True, slots=True)
class FtsQueryTrace:
    meta: QueryMeta
    fts: FtsStageTrace
    final: tuple[FinalResultEntry, ...]


@dataclass(frozen=True, slots=True)
class VectorQueryTrace:
    meta: QueryMeta
    readiness: ManifestReadiness
    vector: VectorStageTrace
    rerank: RerankStageTrace | None
    final: tuple[FinalResultEntry, ...]


@dataclass(frozen=True, slots=True)
class HybridQueryTrace:
    meta: QueryMeta
    readiness: ManifestReadiness
    fts: FtsStageTrace
    vector: VectorStageTrace
    fusion: FusionStageTrace
    rerank: RerankStageTrace | None
    final: tuple[FinalResultEntry, ...]


type QueryTrace = FtsQueryTrace | VectorQueryTrace | HybridQueryTrace


@dataclass
class SearchTraceCollector:
    """Mutable call-scoped accumulator, frozen into a mode-specific trace at the boundary."""

    vector: VectorStageTrace | None = None
    fts: FtsStageTrace | None = None
    fusion: FusionStageTrace | None = None
    rerank: RerankStageTrace | None = None
    readiness: ManifestReadiness | None = None
    stable_pool_refetched: bool = False
    # Rendered from the exact prepared query the repository executed (including
    # legacy note-type expansion), so the trace never re-derives its criteria.
    executed_query_description: str | None = None


# --- Pure stage builders ---


def build_vector_stage(
    *,
    previous: VectorStageTrace | None = None,
    candidate_limit: int | None = None,
    adapter_match_count: int | None = None,
    hydrated_count: int | None = None,
    drops: Sequence[HydrationDropped] | None = None,
    effective_min_similarity: float | None = None,
    min_similarity_source: MinSimilaritySource | None = None,
    threshold_rejections: Sequence[BelowThreshold] | None = None,
    filter_rejections: Sequence[FilteredOut] | None = None,
    missing_search_rows: Sequence[MissingSearchRow] | None = None,
    chunk_matches: Mapping[TraceKey, Sequence[tuple[str, float, int | None]]] | None = None,
    embed_ms: float | None = None,
    vector_query_ms: float | None = None,
) -> VectorStageTrace:
    """Freeze vector-stage values already captured by the executing pipeline."""
    if previous is None and (
        candidate_limit is None or adapter_match_count is None or hydrated_count is None
    ):
        raise ValueError("An initial vector stage requires candidate and adapter counts.")

    previous_candidate_limit = previous.candidate_limit if previous is not None else 0
    previous_adapter_match_count = previous.adapter_match_count if previous is not None else 0
    previous_hydrated_count = previous.hydrated_count if previous is not None else 0
    previous_chunk_matches: dict[TraceKey, list[tuple[str, float, int | None]]] = {}
    if previous is not None:
        for match in previous.chunk_matches:
            previous_chunk_matches.setdefault(match.key, []).append(
                (match.chunk_key, match.similarity, match.entity_id)
            )
    effective_chunk_matches = chunk_matches if chunk_matches is not None else previous_chunk_matches
    return VectorStageTrace(
        candidate_limit=(
            candidate_limit if candidate_limit is not None else previous_candidate_limit
        ),
        adapter_match_count=(
            adapter_match_count if adapter_match_count is not None else previous_adapter_match_count
        ),
        hydrated_count=(hydrated_count if hydrated_count is not None else previous_hydrated_count),
        drops=tuple(drops if drops is not None else (previous.drops if previous else ())),
        effective_min_similarity=(
            effective_min_similarity
            if effective_min_similarity is not None
            else (previous.effective_min_similarity if previous else 0.0)
        ),
        min_similarity_source=(
            min_similarity_source
            if min_similarity_source is not None
            else (previous.min_similarity_source if previous else "config")
        ),
        threshold_rejections=tuple(
            threshold_rejections
            if threshold_rejections is not None
            else (previous.threshold_rejections if previous else ())
        ),
        filter_rejections=tuple(
            filter_rejections
            if filter_rejections is not None
            else (previous.filter_rejections if previous else ())
        ),
        missing_search_rows=tuple(
            missing_search_rows
            if missing_search_rows is not None
            else (previous.missing_search_rows if previous else ())
        ),
        chunk_matches=tuple(
            VectorChunkMatch(
                key=key,
                chunk_key=chunk_key,
                similarity=similarity,
                entity_id=entity_id,
            )
            for key, matches in effective_chunk_matches.items()
            for chunk_key, similarity, entity_id in matches
        ),
        embed_ms=embed_ms if embed_ms is not None else (previous.embed_ms if previous else 0.0),
        vector_query_ms=(
            vector_query_ms
            if vector_query_ms is not None
            else (previous.vector_query_ms if previous else 0.0)
        ),
    )


def build_fts_page_stage(
    raw_scores: Sequence[tuple[TraceKey, float]],
    *,
    normalized_scores: Mapping[TraceKey, float] | None = None,
    entity_ids: Mapping[TraceKey, int | None] | None = None,
    fts_max_abs: float | None = None,
    relaxed_fallback_used: bool,
    fts_ms: float | None = None,
) -> FtsStageTrace:
    """Freeze the exact SQL page and optional hybrid normalization.

    ``raw_scores`` is the ordered page as executed — SQLite can serve duplicate
    copies of one logical row, and each occurrence keeps its own rank here so the
    stage never disagrees with the final results built from the same page.
    """
    owners = entity_ids or {}
    raw = tuple(
        FtsScore(key=key, score=score, rank=rank, entity_id=owners.get(key))
        for rank, (key, score) in enumerate(raw_scores, start=1)
    )
    normalized = (
        tuple(
            FtsScore(key=key, score=score, rank=rank, entity_id=owners.get(key))
            for rank, (key, score) in enumerate(normalized_scores.items(), start=1)
        )
        if normalized_scores is not None
        else None
    )
    return FtsStageTrace(
        raw_scores=raw,
        normalized_scores=normalized,
        fts_max_abs=fts_max_abs,
        result_count=len(raw),
        relaxed_fallback_used=relaxed_fallback_used,
        fts_ms=fts_ms,
    )


def build_fusion_stage(
    *,
    formula_version: str,
    bonus: float,
    fts_scores: Mapping[TraceKey, float],
    fts_ranks: Mapping[TraceKey, int],
    vector_scores: Mapping[TraceKey, float],
    vector_ranks: Mapping[TraceKey, int],
    ranked_scores: Sequence[tuple[TraceKey, float]],
    fusion_ms: float,
) -> FusionStageTrace:
    """Freeze each source leg and the exact fused ordering."""
    return FusionStageTrace(
        formula_version=formula_version,
        bonus=bonus,
        entries=tuple(
            FusionEntry(
                key=key,
                fts_score=fts_scores.get(key),
                fts_rank=(fts_ranks[key] + 1 if key in fts_ranks else None),
                vector_score=vector_scores.get(key),
                vector_rank=(vector_ranks[key] + 1 if key in vector_ranks else None),
                fused_score=score,
                fused_rank=rank,
                dual_source=key in fts_scores and key in vector_scores,
            )
            for rank, (key, score) in enumerate(ranked_scores, start=1)
        ),
        fusion_ms=fusion_ms,
    )


def build_rerank_stage(
    *,
    provider_model: str,
    reranker_candidates: int,
    pre_rerank_scores: Mapping[TraceKey, float],
    pool_keys: Sequence[TraceKey],
    rerank_scores: Mapping[TraceKey, float],
    post_rerank_rows: Sequence[tuple[TraceKey, float]],
    demoted_scores: Mapping[TraceKey, float],
    tail_floor: float,
    stable_pool_refetched: bool,
    rerank_ms: float,
) -> RerankStageTrace:
    """Freeze pre-rewrite scores and the final pool-plus-demoted-tail ordering."""
    pre_ranks = {key: rank for rank, key in enumerate(pre_rerank_scores, start=1)}
    return RerankStageTrace(
        provider_model=provider_model,
        pool_size=len(pool_keys),
        reranker_candidates=reranker_candidates,
        entries=tuple(
            RerankEntry(
                key=key,
                pre_rerank_rank=pre_ranks[key],
                pre_rerank_score=pre_rerank_scores[key],
                rerank_score=rerank_scores.get(key),
                post_rerank_rank=rank,
                demoted_score=demoted_scores.get(key),
            )
            for rank, (key, _score) in enumerate(post_rerank_rows, start=1)
        ),
        tail_floor=tail_floor,
        stable_pool_refetched=stable_pool_refetched,
        rerank_ms=rerank_ms,
    )


def finalize_query_trace(
    collector: SearchTraceCollector,
    meta: QueryMeta,
    final: Sequence[FinalResultEntry],
    mode: RetrievalMode,
) -> QueryTrace:
    """Freeze the collector into the only valid stage combination for ``mode``."""
    final_entries = tuple(final)
    match mode:
        case "fts":
            if collector.fts is None:
                raise ValueError("Cannot finalize FTS query trace without an FTS stage.")
            if any(
                stage is not None
                for stage in (
                    collector.readiness,
                    collector.vector,
                    collector.fusion,
                    collector.rerank,
                )
            ):
                raise ValueError("FTS query trace contains incompatible semantic stages.")
            return FtsQueryTrace(meta=meta, fts=collector.fts, final=final_entries)
        case "vector":
            if collector.readiness is None or collector.vector is None:
                raise ValueError(
                    "Cannot finalize vector query trace without readiness and vector stages."
                )
            if collector.fts is not None or collector.fusion is not None:
                raise ValueError("Vector query trace contains incompatible FTS or fusion stages.")
            return VectorQueryTrace(
                meta=meta,
                readiness=collector.readiness,
                vector=collector.vector,
                rerank=collector.rerank,
                final=final_entries,
            )
        case "hybrid":
            if (
                collector.readiness is None
                or collector.fts is None
                or collector.vector is None
                or collector.fusion is None
            ):
                raise ValueError(
                    "Cannot finalize hybrid query trace without readiness, FTS, vector, and "
                    "fusion stages."
                )
            return HybridQueryTrace(
                meta=meta,
                readiness=collector.readiness,
                fts=collector.fts,
                vector=collector.vector,
                fusion=collector.fusion,
                rerank=collector.rerank,
                final=final_entries,
            )


# --- Trace-only manifest reads ---


@dataclass(frozen=True, slots=True)
class HydrationDropKey:
    entity_id: int
    chunk_key: str
    similarity: float
    configured_index: str
    configured_model: str


async def read_manifest_readiness(
    session: Any,
    project_id: int,
    vector_index: str,
    embedding_model: str,
) -> ManifestReadiness:
    """Count configured readiness and rows stored under another vector identity."""
    from sqlalchemy import text

    readiness_result = await session.execute(
        text(
            "SELECT embedding_status, COUNT(*) AS row_count "
            "FROM search_vector_chunks WHERE project_id = :project_id "
            "AND vector_index = :vector_index AND embedding_model = :embedding_model "
            "GROUP BY embedding_status"
        ),
        {
            "project_id": project_id,
            "vector_index": vector_index,
            "embedding_model": embedding_model,
        },
    )
    counts = {
        str(row["embedding_status"]): int(row["row_count"])
        for row in readiness_result.mappings().all()
    }
    other_result = await session.execute(
        text(
            "SELECT COUNT(*) FROM search_vector_chunks WHERE project_id = :project_id "
            "AND (vector_index <> :vector_index OR embedding_model <> :embedding_model)"
        ),
        {
            "project_id": project_id,
            "vector_index": vector_index,
            "embedding_model": embedding_model,
        },
    )
    return ManifestReadiness(
        configured_index=vector_index,
        configured_model=embedding_model,
        ready_rows=counts.get("ready", 0),
        pending_rows=counts.get("pending", 0),
        other_identity_rows=int(other_result.scalar_one()),
    )


async def classify_hydration_drops(
    session: Any,
    project_id: int,
    dropped_keys: Sequence[HydrationDropKey],
) -> tuple[HydrationDropped, ...]:
    """Classify adapter hits rejected by authoritative manifest hydration."""
    if not dropped_keys:
        return ()

    from sqlalchemy import text

    stored_by_key: dict[tuple[int, str], Any] = {}
    for batch_start in range(
        0,
        len(dropped_keys),
        HYDRATION_DROP_CLASSIFICATION_BATCH_SIZE,
    ):
        batch = dropped_keys[batch_start : batch_start + HYDRATION_DROP_CLASSIFICATION_BATCH_SIZE]
        params: dict[str, object] = {"project_id": project_id}
        predicates: list[str] = []
        for index, dropped in enumerate(batch):
            params[f"entity_id_{index}"] = dropped.entity_id
            params[f"chunk_key_{index}"] = dropped.chunk_key
            predicates.append(
                f"(entity_id = :entity_id_{index} AND chunk_key = :chunk_key_{index})"
            )

        # Constraint: unhealthy indexes can return thousands of dropped adapter hits.
        # SQLite caps expression depth and both backends cap bind parameters, so classify
        # in the same fixed-size windows used by authoritative manifest hydration.
        result = await session.execute(
            text(
                "SELECT entity_id, chunk_key, embedding_model, vector_index, embedding_status "
                "FROM search_vector_chunks WHERE project_id = :project_id AND ("
                + " OR ".join(predicates)
                + ")"
            ),
            params,
        )
        stored_by_key.update(
            {(int(row["entity_id"]), str(row["chunk_key"])): row for row in result.mappings().all()}
        )

    classified: list[HydrationDropped] = []
    for dropped in dropped_keys:
        stored = stored_by_key.get((dropped.entity_id, dropped.chunk_key))
        if stored is None:
            reason: DropReason = "not_in_manifest"
            stored_model = None
            stored_index = None
        else:
            stored_model = str(stored["embedding_model"])
            stored_index = str(stored["vector_index"])
            if stored_model != dropped.configured_model:
                reason = "model_mismatch"
            elif stored_index != dropped.configured_index:
                reason = "index_mismatch"
            elif str(stored["embedding_status"]) == "pending":
                reason = "pending"
            else:
                # Trigger: derived readiness changed between hydration and classification.
                # Why: PostgreSQL statement snapshots and concurrent embedding sync can make
                # the follow-up read observe a now-ready row that the executing query dropped.
                # Outcome: report the race explicitly without locking or failing inspection.
                reason = "readiness_changed"
        classified.append(
            HydrationDropped(
                entity_id=dropped.entity_id,
                chunk_key=dropped.chunk_key,
                similarity=dropped.similarity,
                reason=reason,
                stored_model=stored_model,
                stored_index=stored_index,
            )
        )
    return tuple(classified)
