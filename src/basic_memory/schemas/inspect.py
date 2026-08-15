"""API schemas for note-level retrieval inspection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Self, assert_never

from pydantic import BaseModel, ConfigDict, Field, model_validator

from basic_memory.schemas.search import SearchQuery, SearchRetrievalMode

if TYPE_CHECKING:
    from basic_memory.repository.search_trace import QueryTrace, Rejection, TraceKey

type ChunkStatus = Literal["ready", "pending", "stale", "orphaned"]
type InspectFreshness = Literal[
    "fresh",
    "not_indexed",
    "index_behind_rows",
    "rows_behind_file",
    "unknown",
]
type InspectFileWriteStatus = Literal[
    "pending",
    "writing",
    "synced",
    "failed",
    "external_change_detected",
]


class InspectChunksRequest(BaseModel):
    """Request to inspect the retrieval projections for one note identifier."""

    identifier: str


class InspectChunkReadiness(BaseModel):
    """Mutually exclusive vector-chunk readiness counts.

    ``missing`` counts current chunks with no stored manifest row; ``total`` covers
    stored rows only.
    """

    total: int
    ready: int
    pending: int
    stale: int
    orphaned: int
    missing: int


class InspectChunk(BaseModel):
    """One vector chunk stored for a search row."""

    chunk_key: str
    ordinal: int
    text: str
    source_hash: str
    embedding_model: str
    vector_index: str
    status: ChunkStatus
    updated_at: datetime


class InspectSearchRow(BaseModel):
    """One search row and its current stored vector chunks."""

    type: str
    id: int
    title: str | None
    category: str | None
    relation_type: str | None
    content_preview: str | None
    chunks: list[InspectChunk]


class InspectDetachedSearchRow(BaseModel):
    """Stored chunks grouped by a source search row that no longer exists."""

    type: str
    id: int
    source_row_gone: Literal[True] = True
    chunks: list[InspectChunk]


class InspectIndexBehindRowsDetail(BaseModel):
    """Evidence that chunks trail, cannot serve, or do not cover current search rows."""

    model_config = ConfigDict(extra="forbid")

    entity_fingerprint_indexed: str | list[str] | None
    entity_fingerprint_current: str
    missing_chunk_count: int


class InspectRowsBehindFileDetail(BaseModel):
    """File and note-content evidence used for file-to-row freshness."""

    model_config = ConfigDict(extra="forbid")

    entity_checksum: str | None
    current_file_checksum: str | None
    db_checksum: str | None
    file_checksum: str | None
    file_write_status: InspectFileWriteStatus | None


type InspectFreshnessDetail = InspectIndexBehindRowsDetail | InspectRowsBehindFileDetail


class InspectChunksResponse(BaseModel):
    """Note identity, chunk readiness, and search-row decomposition."""

    entity_id: int
    external_id: str
    permalink: str | None
    file_path: str
    title: str
    entity_checksum: str | None
    configured_embedding_model: str
    configured_vector_index: str
    readiness: InspectChunkReadiness
    entity_fingerprint_indexed: str | list[str] | None
    entity_fingerprint_current: str
    stale: bool = Field(
        description=(
            "Whether stored vector chunks trail or cannot serve current search rows. This "
            "signal remains independent when upstream file freshness is unknown."
        )
    )
    freshness: InspectFreshness = Field(
        description=(
            "Upstream-first file-to-row-to-index classification. Unknown can coexist with "
            "stale=true when file bytes are unreadable but chunk fingerprints prove index lag."
        )
    )
    freshness_detail: InspectFreshnessDetail | None = Field(
        description=(
            "Evidence for the selected freshness state. When freshness is unknown, consult "
            "stale, readiness, and the fingerprint fields for independent row-to-index evidence."
        )
    )
    rows: list[InspectSearchRow]
    detached: list[InspectDetachedSearchRow]

    @model_validator(mode="after")
    def validate_freshness_detail(self) -> Self:
        """Keep the derived freshness value and its evidence in a valid combination."""
        match self.freshness:
            case "fresh" | "not_indexed":
                detail_is_valid = self.freshness_detail is None
            case "index_behind_rows":
                detail_is_valid = isinstance(
                    self.freshness_detail,
                    InspectIndexBehindRowsDetail,
                )
            case "rows_behind_file" | "unknown":
                detail_is_valid = isinstance(
                    self.freshness_detail,
                    InspectRowsBehindFileDetail,
                )
            case unexpected:  # pragma: no cover - InspectFreshness is exhaustive
                assert_never(unexpected)

        if not detail_is_valid:
            raise ValueError(f"Invalid detail for freshness={self.freshness}")
        return self


# --- Query execution trace ---


class InspectQueryRequest(BaseModel):
    """One search request and the bounded result window to explain."""

    query: SearchQuery
    limit: int = Field(default=10, ge=1)
    offset: int = Field(default=0, ge=0)


class InspectQueryWindow(BaseModel):
    limit: int
    offset: int
    candidate_limit: int
    rerank_pool: int


class InspectQueryReranker(BaseModel):
    enabled: bool
    model: str | None
    candidates: int
    applied: bool
    skipped_reason: str | None


class InspectQueryEngine(BaseModel):
    embedding_model: str
    vector_index: str
    # None = not applicable: an FTS-only execution never reads the vector manifest,
    # so zeros here would misreport a populated index as empty.
    ready_rows: int | None
    pending_rows: int | None
    other_identity_rows: int | None
    fusion_formula: str
    min_similarity: float
    min_similarity_source: Literal["query", "config"]
    reranker: InspectQueryReranker


class InspectQueryStage(BaseModel):
    name: str
    count_in: int
    count_out: int
    dropped: int | None
    ms: float | None
    # Only the fts stage sets this: True means strict matching found nothing and the
    # displayed results came from the relaxed OR/prefix retry.
    relaxed_fallback_used: bool | None = None


class InspectMatchedChunk(BaseModel):
    chunk_key: str
    similarity: float


class InspectDroppedChunk(BaseModel):
    chunk_key: str
    similarity: float
    reason: Literal[
        "not_in_manifest",
        "pending",
        "model_mismatch",
        "index_mismatch",
        "readiness_changed",
        "malformed_key",
    ]
    stored_model: str | None
    stored_index: str | None


class InspectQueryRejectionDetail(BaseModel):
    reason: str
    chunk_key: str | None = None
    similarity: float | None = None
    threshold: float | None = None
    stored_model: str | None = None
    stored_index: str | None = None
    rank: int | None = None
    score: float | None = None


class InspectQueryScores(BaseModel):
    fts_raw: float | None = None
    fts_normalized: float | None = None
    fts_rank: int | None = None
    vector_similarity: float | None = None
    vector_rank: int | None = None
    fused_score: float | None = None
    fused_rank: int | None = None
    dual_source: bool | None = None
    pre_rerank_rank: int | None = None
    pre_rerank_score: float | None = None
    rerank_score: float | None = None
    post_rerank_rank: int | None = None
    final_rank: int | None = None
    final_score: float | None = None


type InspectDisposition = Literal[
    "returned",
    "below_threshold",
    "not_in_manifest",
    "pending",
    "model_mismatch",
    "index_mismatch",
    "readiness_changed",
    "malformed_key",
    "filtered_out",
    "missing_search_row",
    "beyond_page_window",
]


class InspectQueryCandidate(BaseModel):
    type: str | None
    id: int | None
    external_id: str | None
    title: str | None
    permalink: str | None
    file_path: str | None
    disposition: InspectDisposition
    rejection_detail: InspectQueryRejectionDetail | None
    matched_chunks: list[InspectMatchedChunk]
    dropped_chunks: list[InspectDroppedChunk]
    scores: InspectQueryScores


class InspectQueryTimings(BaseModel):
    total: float
    embedding: float | None
    vector_query: float | None
    fts: float | None
    fusion: float | None
    rerank: float | None


class InspectQueryResponse(BaseModel):
    """Machine-readable flattening of one execution-native query trace."""

    query: str
    retrieval_mode: SearchRetrievalMode
    project_id: int
    window: InspectQueryWindow
    engine: InspectQueryEngine
    stages: list[InspectQueryStage]
    candidates: list[InspectQueryCandidate]
    timings_ms: InspectQueryTimings


@dataclass(frozen=True, slots=True)
class _ForeignOwnerDropKey:
    """Identity for a parseable drop whose owner differs from the live row's owner.

    Manifest uniqueness includes entity_id, so one parseable chunk_key can exist under
    two owners (a stale adapter hit surviving a search-row ID reuse); merging their
    evidence would misattribute one entity's miss — or external id — to the other.
    """

    entity_id: int | None
    key: TraceKey


@dataclass(frozen=True, slots=True)
class _MalformedDropKey:
    """Identity for a dropped chunk whose key failed to parse, scoped to its owner.

    Two entities can serve identically malformed keys; the raw key alone would merge
    them and misattribute one entity's drop evidence to the other.
    """

    entity_id: int | None
    raw_chunk_key: str


@dataclass(slots=True)
class _CandidateTrace:
    key: TraceKey | None
    raw_chunk_key: str | None = None
    entity_id: int | None = None
    title: str | None = None
    permalink: str | None = None
    file_path: str | None = None
    rejection: Rejection | None = None
    matched_chunks: list[InspectMatchedChunk] = field(default_factory=list)
    dropped_chunks: list[InspectDroppedChunk] = field(default_factory=list)
    scores: InspectQueryScores = field(default_factory=InspectQueryScores)


def _best_similarity(current: float | None, observed: float) -> float:
    """Track the best similarity without clamping negative cosine scores at zero."""
    return observed if current is None else max(current, observed)


def _parse_trace_chunk_key(chunk_key: str) -> TraceKey:
    parts = chunk_key.split(":")
    if len(parts) < 3:
        raise ValueError(f"Invalid traced chunk key: {chunk_key!r}")
    return parts[0], int(parts[1])


def _rejection_disposition(rejection: Rejection) -> InspectDisposition:
    from basic_memory.repository.search_trace import (
        BelowThreshold,
        BeyondPageWindow,
        FilteredOut,
        HydrationDropped,
        MissingSearchRow,
    )

    match rejection:
        case BelowThreshold():
            return "below_threshold"
        case HydrationDropped(reason=reason):
            return reason
        case FilteredOut():
            return "filtered_out"
        case MissingSearchRow():
            return "missing_search_row"
        case BeyondPageWindow():
            return "beyond_page_window"
        case unexpected:  # pragma: no cover - Rejection is a closed union
            assert_never(unexpected)


def _rejection_detail(rejection: Rejection) -> InspectQueryRejectionDetail:
    from basic_memory.repository.search_trace import (
        BelowThreshold,
        BeyondPageWindow,
        FilteredOut,
        HydrationDropped,
        MissingSearchRow,
    )

    match rejection:
        case BelowThreshold(similarity=similarity, threshold=threshold):
            return InspectQueryRejectionDetail(
                reason="below_threshold",
                similarity=similarity,
                threshold=threshold,
            )
        case HydrationDropped(
            chunk_key=chunk_key,
            similarity=similarity,
            reason=reason,
            stored_model=stored_model,
            stored_index=stored_index,
        ):
            return InspectQueryRejectionDetail(
                reason=reason,
                chunk_key=chunk_key,
                similarity=similarity,
                stored_model=stored_model,
                stored_index=stored_index,
            )
        case FilteredOut():
            return InspectQueryRejectionDetail(reason="filtered_out")
        case MissingSearchRow():
            return InspectQueryRejectionDetail(reason="missing_search_row")
        case BeyondPageWindow(rank=rank, score=score):
            return InspectQueryRejectionDetail(
                reason="beyond_page_window",
                rank=rank,
                score=score,
            )
        case unexpected:  # pragma: no cover - Rejection is a closed union
            assert_never(unexpected)


def query_trace_response(
    trace: QueryTrace,
    entity_external_id_lookup: Mapping[int, str] | None = None,
) -> InspectQueryResponse:
    """Flatten a closed query trace without re-running or inferring retrieval stages."""
    from basic_memory.repository.search_trace import (
        BeyondPageWindow,
        FtsQueryTrace,
        HydrationDropped,
        HybridQueryTrace,
        VectorQueryTrace,
    )

    candidates: dict[TraceKey | _MalformedDropKey | _ForeignOwnerDropKey, _CandidateTrace] = {}
    external_ids = entity_external_id_lookup or {}

    def candidate(key: TraceKey) -> _CandidateTrace:
        return candidates.setdefault(key, _CandidateTrace(key=key))

    fts = trace.fts if isinstance(trace, (FtsQueryTrace, HybridQueryTrace)) else None
    vector = trace.vector if isinstance(trace, (VectorQueryTrace, HybridQueryTrace)) else None
    fusion = trace.fusion if isinstance(trace, HybridQueryTrace) else None
    rerank = trace.rerank if isinstance(trace, (VectorQueryTrace, HybridQueryTrace)) else None
    readiness = trace.readiness if isinstance(trace, (VectorQueryTrace, HybridQueryTrace)) else None

    # Seed owners already known from the final results before any drop processing:
    # a row returned via FTS alone has no vector chunk match to establish its owner,
    # and foreign-owner drop detection needs that identity to keep a stale same-key
    # hit from attaching to the returned row.
    for result in trace.final:
        if result.entity_id is not None:
            candidate(result.key).entity_id = result.entity_id

    if fts is not None:
        fts_rank_offset = trace.meta.offset if isinstance(trace, FtsQueryTrace) else 0
        for score in fts.raw_scores:
            entry = candidate(score.key)
            if entry.entity_id is None:
                entry.entity_id = score.entity_id
            # Duplicate SQL-page occurrences share one logical candidate; the page is
            # rank-ordered, so the first occurrence carries the row's best source rank.
            if entry.scores.fts_rank is None:
                entry.scores.fts_raw = score.score
                entry.scores.fts_rank = score.rank + fts_rank_offset
        for score in fts.normalized_scores or ():
            candidate(score.key).scores.fts_normalized = score.score

    if vector is not None:
        best_vector_scores: dict[TraceKey, float] = {}
        for match in vector.chunk_matches:
            entry = candidate(match.key)
            if entry.entity_id is None:
                entry.entity_id = match.entity_id
            entry.matched_chunks.append(
                InspectMatchedChunk(
                    chunk_key=match.chunk_key,
                    similarity=match.similarity,
                )
            )
            best_vector_scores[match.key] = _best_similarity(
                best_vector_scores.get(match.key),
                match.similarity,
            )
        surviving_vector_keys = set(best_vector_scores)
        for rejection in vector.drops:
            try:
                key = _parse_trace_chunk_key(rejection.chunk_key)
            except (TypeError, ValueError):
                entry = candidates.setdefault(
                    _MalformedDropKey(rejection.entity_id, rejection.chunk_key),
                    _CandidateTrace(key=None, raw_chunk_key=rejection.chunk_key),
                )
                entry.entity_id = rejection.entity_id
                entry.rejection = rejection
                entry.dropped_chunks.append(
                    InspectDroppedChunk(
                        chunk_key=rejection.chunk_key,
                        similarity=rejection.similarity,
                        reason=rejection.reason,
                        stored_model=rejection.stored_model,
                        stored_index=rejection.stored_index,
                    )
                )
                entry.scores.vector_similarity = _best_similarity(
                    entry.scores.vector_similarity,
                    rejection.similarity,
                )
                continue
            entry = candidate(key)
            # Trigger: this drop names a different owner than the candidate already has.
            # Why: the same parseable chunk_key can exist under two entity_ids; merging
            # would overwrite the owner and misattribute drop evidence or external ids.
            # Outcome: foreign-owner drops get their own candidate, scored and rejected
            # from their drops alone (they have no hydrated chunks by construction).
            if entry.entity_id is not None and rejection.entity_id != entry.entity_id:
                foreign = candidates.setdefault(
                    _ForeignOwnerDropKey(rejection.entity_id, key),
                    _CandidateTrace(key=key),
                )
                foreign.entity_id = rejection.entity_id
                foreign.dropped_chunks.append(
                    InspectDroppedChunk(
                        chunk_key=rejection.chunk_key,
                        similarity=rejection.similarity,
                        reason=rejection.reason,
                        stored_model=rejection.stored_model,
                        stored_index=rejection.stored_index,
                    )
                )
                current_rejection = foreign.rejection
                if current_rejection is None or (
                    isinstance(current_rejection, HydrationDropped)
                    and (
                        rejection.similarity > current_rejection.similarity
                        or (
                            rejection.similarity == current_rejection.similarity
                            and rejection.chunk_key < current_rejection.chunk_key
                        )
                    )
                ):
                    foreign.rejection = rejection
                foreign.scores.vector_similarity = _best_similarity(
                    foreign.scores.vector_similarity,
                    rejection.similarity,
                )
                continue
            entry.entity_id = rejection.entity_id
            entry.dropped_chunks.append(
                InspectDroppedChunk(
                    chunk_key=rejection.chunk_key,
                    similarity=rejection.similarity,
                    reason=rejection.reason,
                    stored_model=rejection.stored_model,
                    stored_index=rejection.stored_index,
                )
            )
            # Trigger: one row has both a ready chunk and a rejected sibling chunk.
            # Why: retrieval ranks the row from its ready chunks, so promoting a sibling's
            # drop to the row would make the trace disagree with the result execution.
            # Outcome: retain the dropped chunk as evidence, but reject and score the row
            # from drops only when no hydrated chunk for that row survived.
            if key not in surviving_vector_keys:
                current_rejection = entry.rejection
                if current_rejection is None or (
                    isinstance(current_rejection, HydrationDropped)
                    and (
                        rejection.similarity > current_rejection.similarity
                        or (
                            rejection.similarity == current_rejection.similarity
                            and rejection.chunk_key < current_rejection.chunk_key
                        )
                    )
                ):
                    entry.rejection = rejection
                best_vector_scores[key] = _best_similarity(
                    best_vector_scores.get(key),
                    rejection.similarity,
                )
        for key, score in best_vector_scores.items():
            candidate(key).scores.vector_similarity = score
        for rejection in vector.threshold_rejections:
            candidate(rejection.key).rejection = rejection
        for rejection in vector.filter_rejections:
            candidate(rejection.key).rejection = rejection
        for rejection in vector.missing_search_rows:
            candidate(rejection.key).rejection = rejection
        surviving_vector_scores = [
            (key, score)
            for key, score in best_vector_scores.items()
            if candidate(key).rejection is None
        ]
        surviving_vector_scores.sort(key=lambda item: item[1], reverse=True)
        for rank, (key, _score) in enumerate(surviving_vector_scores, start=1):
            candidate(key).scores.vector_rank = rank

    if fusion is not None:
        for fused in fusion.entries:
            entry = candidate(fused.key)
            # Reaching fusion means at least one retrieval leg admitted the row. Any
            # vector-only rejection is therefore chunk/leg evidence, not its disposition.
            entry.rejection = None
            entry.scores.fts_normalized = fused.fts_score
            entry.scores.fts_rank = fused.fts_rank
            entry.scores.vector_similarity = fused.vector_score
            entry.scores.vector_rank = fused.vector_rank
            entry.scores.fused_score = fused.fused_score
            entry.scores.fused_rank = fused.fused_rank
            entry.scores.dual_source = fused.dual_source

    if rerank is not None:
        for reranked in rerank.entries:
            entry = candidate(reranked.key)
            entry.scores.pre_rerank_rank = reranked.pre_rerank_rank
            entry.scores.pre_rerank_score = reranked.pre_rerank_score
            entry.scores.rerank_score = reranked.rerank_score
            entry.scores.post_rerank_rank = reranked.post_rerank_rank

    returned_keys = {result.key for result in trace.final}
    ranked_for_page: list[tuple[TraceKey, int, float]]
    if rerank is not None:
        ranked_for_page = [
            (
                entry.key,
                entry.post_rerank_rank,
                entry.rerank_score
                if entry.rerank_score is not None
                else (entry.demoted_score or 0.0),
            )
            for entry in rerank.entries
        ]
    elif fusion is not None:
        ranked_for_page = [
            (entry.key, entry.fused_rank, entry.fused_score) for entry in fusion.entries
        ]
    elif vector is not None:
        ranked_for_page = [
            (entry.key, entry.scores.vector_rank, entry.scores.vector_similarity)
            for entry in candidates.values()
            if entry.rejection is None
            and entry.key is not None
            and entry.scores.vector_rank is not None
            and entry.scores.vector_similarity is not None
        ]
    else:
        ranked_for_page = []

    for key, rank, score in ranked_for_page:
        entry = candidate(key)
        if key not in returned_keys and entry.rejection is None:
            entry.rejection = BeyondPageWindow(key=key, rank=rank, score=score)

    for result in trace.final:
        entry = candidate(result.key)
        entry.entity_id = result.entity_id
        entry.title = result.title
        entry.permalink = result.permalink
        entry.file_path = result.file_path
        entry.rejection = None

    stages: list[InspectQueryStage] = []
    fts_stage = (
        InspectQueryStage(
            name="fts",
            count_in=fts.result_count,
            count_out=fts.result_count,
            dropped=0,
            ms=fts.fts_ms,
            relaxed_fallback_used=fts.relaxed_fallback_used,
        )
        if fts is not None
        else None
    )
    # The hybrid pipeline awaits the FTS leg before embedding and vector retrieval,
    # so the displayed plan lists FTS first there — inverted order would misread the
    # per-stage timings when diagnosing hybrid latency.
    if fts_stage is not None and isinstance(trace, HybridQueryTrace):
        stages.append(fts_stage)
    if vector is not None:
        served_chunk_count = len(vector.chunk_matches)
        collapsed_row_count = len({match.key for match in vector.chunk_matches})
        # Threshold, note-type filters, and search-row hydration reject whole rows
        # after the collapse; without a stage of their own the plan would show more
        # collapsed rows than ranked results with the losses unexplained.
        row_rejection_count = (
            len(vector.threshold_rejections)
            + len(vector.filter_rejections)
            + len(vector.missing_search_rows)
        )
        stages.extend(
            [
                InspectQueryStage(
                    name="embedding",
                    count_in=1,
                    count_out=1,
                    dropped=0,
                    ms=vector.embed_ms,
                ),
                InspectQueryStage(
                    name="vector",
                    count_in=vector.adapter_match_count,
                    count_out=vector.hydrated_count,
                    dropped=max(0, vector.adapter_match_count - vector.hydrated_count),
                    ms=vector.vector_query_ms,
                ),
                InspectQueryStage(
                    name="row_collapse",
                    count_in=served_chunk_count,
                    count_out=collapsed_row_count,
                    dropped=None,
                    ms=None,
                ),
                InspectQueryStage(
                    name="row_filters",
                    count_in=collapsed_row_count,
                    count_out=collapsed_row_count - row_rejection_count,
                    dropped=row_rejection_count,
                    ms=None,
                ),
            ]
        )
        # Trigger: an external-index overfetch hydrated more ready chunks than the
        # candidate window the search consumes.
        # Why: those chunks were truncated, not dropped by hydration — folding them
        # into the vector stage would misattribute healthy chunks as failures.
        # Outcome: the truncation appears as its own stage between vector and
        # row_collapse, keeping every count at a single scope.
        if served_chunk_count < vector.hydrated_count:
            stages.insert(
                len(stages) - 2,
                InspectQueryStage(
                    name="candidate_window",
                    count_in=vector.hydrated_count,
                    count_out=served_chunk_count,
                    dropped=vector.hydrated_count - served_chunk_count,
                    ms=None,
                ),
            )
    if fts_stage is not None and not isinstance(trace, HybridQueryTrace):
        stages.append(fts_stage)
    if fusion is not None:
        stages.append(
            InspectQueryStage(
                name="fusion",
                count_in=len(fusion.entries),
                count_out=len(fusion.entries),
                dropped=0,
                ms=fusion.fusion_ms,
            )
        )
    if rerank is not None:
        stages.append(
            InspectQueryStage(
                name="rerank",
                count_in=len(rerank.entries),
                count_out=len(rerank.entries),
                dropped=0,
                ms=rerank.rerank_ms,
            )
        )

    # SQLite FTS can return duplicate copies of one logical row; the trace's final
    # entries preserve each occurrence, so the response must too — one returned
    # candidate per occurrence, sharing the key's stage data but carrying its own
    # final rank and score.
    returned_final_keys = {result.key for result in trace.final}
    response_candidates = [
        InspectQueryCandidate(
            type=result.key[0],
            id=result.key[1],
            external_id=(
                external_ids.get(result.entity_id) if result.entity_id is not None else None
            ),
            title=result.title,
            permalink=result.permalink,
            file_path=result.file_path,
            disposition="returned",
            rejection_detail=None,
            matched_chunks=candidates[result.key].matched_chunks,
            dropped_chunks=candidates[result.key].dropped_chunks,
            scores=candidates[result.key].scores.model_copy(
                update={"final_rank": result.final_rank, "final_score": result.final_score}
            ),
        )
        for result in trace.final
    ]
    response_candidates += [
        InspectQueryCandidate(
            type=entry.key[0] if entry.key is not None else None,
            id=entry.key[1] if entry.key is not None else None,
            external_id=(
                external_ids.get(entry.entity_id) if entry.entity_id is not None else None
            ),
            title=entry.title,
            permalink=entry.permalink,
            file_path=entry.file_path,
            disposition=(
                "returned" if entry.rejection is None else _rejection_disposition(entry.rejection)
            ),
            rejection_detail=(
                _rejection_detail(entry.rejection) if entry.rejection is not None else None
            ),
            matched_chunks=entry.matched_chunks,
            dropped_chunks=entry.dropped_chunks,
            scores=entry.scores,
        )
        for entry in sorted(
            # A rejected candidate is a miss even when it shares its row key with a
            # returned result (foreign-owner drops); only the returned rows themselves
            # (rejection cleared by the final loop) are excluded here.
            (
                entry
                for entry in candidates.values()
                if entry.rejection is not None or entry.key not in returned_final_keys
            ),
            key=lambda item: (
                item.scores.post_rerank_rank or 1_000_000,
                _rejection_disposition(item.rejection) if item.rejection is not None else "",
                item.key is None,
                item.key or ("", 0),
                item.raw_chunk_key or "",
            ),
        )
    ]

    return InspectQueryResponse(
        query=trace.meta.query_text,
        retrieval_mode=SearchRetrievalMode(trace.meta.retrieval_mode),
        project_id=trace.meta.project_id,
        window=InspectQueryWindow(
            limit=trace.meta.limit,
            offset=trace.meta.offset,
            candidate_limit=trace.meta.candidate_limit,
            rerank_pool=trace.meta.rerank_pool_size,
        ),
        engine=InspectQueryEngine(
            embedding_model=trace.meta.embedding_model,
            vector_index=trace.meta.vector_index,
            ready_rows=readiness.ready_rows if readiness is not None else None,
            pending_rows=readiness.pending_rows if readiness is not None else None,
            other_identity_rows=(readiness.other_identity_rows if readiness is not None else None),
            fusion_formula=trace.meta.fusion_formula_version,
            min_similarity=trace.meta.min_similarity,
            min_similarity_source=trace.meta.min_similarity_source,
            reranker=InspectQueryReranker(
                enabled=trace.meta.reranker.enabled,
                model=trace.meta.reranker.model,
                candidates=trace.meta.reranker.candidates,
                applied=trace.meta.rerank_applied,
                skipped_reason=trace.meta.rerank_skipped_reason,
            ),
        ),
        stages=stages,
        candidates=response_candidates,
        timings_ms=InspectQueryTimings(
            total=trace.meta.total_ms,
            embedding=vector.embed_ms if vector is not None else None,
            vector_query=vector.vector_query_ms if vector is not None else None,
            fts=fts.fts_ms if fts is not None else None,
            fusion=fusion.fusion_ms if fusion is not None else None,
            rerank=rerank.rerank_ms if rerank is not None else None,
        ),
    )
