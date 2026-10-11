"""Scoped search retrieval shared by every route.

``SearchReader`` runs one prepared query over one ``ProjectScope``: the engine's
full-text statement for FTS mode, and vector or hybrid retrieval through
``SemanticSearch`` when the semantic stack is present. Neither class owns indexing,
manifest writes, or table lifecycle. A repository builds a reader per call from its
current state, so a repository whose semantic stack was disabled at startup hands the
reader the matching capability set; a route reading several projects at once builds
one directly over a wider scope.
"""

import time
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from typing import Any, assert_never

import logfire
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.rerank_provider import (
    RerankProvider,
    build_rerank_document,
    demote_tail_scores,
    validate_rerank_scores,
)
from basic_memory.repository.search_filters import FtsBackend
from basic_memory.repository.search_index_row import (
    SearchIndexKey,
    SearchIndexRow,
    capped_snippet_columns,
    snippet_length_columns,
)
from basic_memory.repository.search_query import PreparedSearchQuery
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.search_trace import (
    BelowThreshold,
    FilteredOut,
    HydrationDropKey,
    MissingSearchRow,
    SearchTraceCollector,
    build_fts_page_stage,
    build_fusion_stage,
    build_rerank_stage,
    build_vector_stage,
    classify_hydration_drops,
    read_manifest_readiness,
)
from basic_memory.repository.semantic_errors import SemanticSearchDisabledError
from basic_memory.repository.semantic_vector_index import (
    SemanticVectorIndex,
    VectorAdmission,
    VectorKey,
    VectorMatch,
    prefixed_binds,
)
from basic_memory.schemas.search import SearchRetrievalMode

# --- Retrieval constants ---

# Adapters that share the authoritative SQL database. Everything else stores vectors
# outside it and needs the stale-hit overfetch below.
BUILT_IN_VECTOR_INDEX_NAMES = frozenset({"pgvector", "sqlite-vec"})
VECTOR_FILTER_SCAN_LIMIT = 50000
# The shared bind-parameter bound for any statement that carries a list of vector
# candidate keys. Both engines cap bind parameters (asyncpg at 32767), so every such
# list — manifest hydration and the filter intersection alike — is split at this size.
VECTOR_HYDRATION_BATCH_SIZE = 250
# Over-fetch factor for the rerank candidate chunk pool: chunks collapse to unique
# (type, id) rows before reranking, so fetch several times reranker_candidates chunks
# to keep enough unique documents in the rerank window.
RERANK_POOL_CHUNK_FANOUT = 4
FUSION_BONUS = 0.3
FUSION_FORMULA_VERSION = "max+0.3*min/v1"
FTS_GATE_THRESHOLD = 0.0
TOP_CHUNKS_PER_RESULT = 5
SMALL_NOTE_CONTENT_LIMIT = 2000
# Most chunks one excerpt-ranking query asks the index for. sqlite-vec answers at most
# 4096 neighbours per query, so the returned rows' chunks are ranked in batches under it.
EXCERPT_RANKING_CHUNK_BUDGET = 4000


# The manifest conditions under which semantic retrieval will use a stored vector.
# Vector hydration admits exactly these rows, so anything failing them is invisible
# to search: a chunk left behind by an embedding-model or vector-index change, or one
# still pending. Readiness reporting must apply the same predicate — calling such a
# row "embedded" would report an index settled that retrieval cannot answer from,
# which is the class of lie #1414 exists to remove.
# Callers bind :vector_index and :embedding_model; the scope binds its own IDs.
def current_vector_manifest_predicate(scope: ProjectScope, params: dict[str, Any]) -> str:
    """SQL admitting only manifest rows retrieval can answer from, within ``scope``."""
    return (
        f"{scope.predicate('project_id', params)} "
        "AND vector_index = :vector_index "
        "AND embedding_model = :embedding_model "
        "AND embedding_status = 'ready'"
    )


def vector_eligible(query: PreparedSearchQuery) -> bool:
    """Whether a query carries text to embed and no identity filter.

    Vector and hybrid retrieval score an embedding of the query text; a permalink or
    title lookup has nothing to embed and answers exactly through the full-text path.
    """
    text_value = (query.search_text or "").strip()
    return (
        bool(text_value)
        and text_value != "*"
        and not query.permalink
        and not query.permalink_match
        and not query.title
    )


def rerank_document_text(row: SearchIndexRow, max_chars: int) -> str:
    """Build the document text handed to the cross-encoder for one candidate.

    Prefer the matched chunk (the most relevant passage of a large note), falling
    back to the stored snippet.
    """
    body = row.matched_chunk_text or row.content_snippet or ""
    return build_rerank_document(row.title, body, max_chars)


def demote_tail(tail: list[SearchIndexRow], floor: float) -> list[SearchIndexRow]:
    """Rescore un-reranked tail rows at or below the floor, preserving their order.

    The reranked pool carries [0, 1] relevance scores while the tail still holds raw
    retrieval scores on a different scale ([0, 1.3] for fused hybrid). Left as is, a
    tail row could outrank a reranked row numerically. Positive floors put the tail
    strictly below the pool; a zero floor yields zeroes because no smaller score
    exists in the public [0, 1] range. The returned pool-plus-tail sequence, rather
    than a later score-only sort, owns that tie-breaking invariant.
    """
    return [
        replace(row, score=score) for row, score in zip(tail, demote_tail_scores(floor, len(tail)))
    ]


# --- Retrieval capabilities ---


@dataclass(frozen=True, slots=True)
class VectorRetrieval:
    """The live semantic stack vector retrieval reads from.

    Present only when semantic search is enabled, a provider is configured, and the
    adapter is bound; absence means the reader answers full-text queries only.
    """

    index: SemanticVectorIndex
    index_name: str
    embedding_provider: EmbeddingProvider
    # The persisted embedding identity manifest rows are keyed by.
    embedding_model: str
    vector_k: int
    min_similarity: float

    @property
    def external(self) -> bool:
        """Whether vectors live outside the SQL database that owns the manifest."""
        return self.index_name not in BUILT_IN_VECTOR_INDEX_NAMES


@dataclass(frozen=True, slots=True)
class Reranking:
    """A configured cross-encoder and the fixed prefix it rescores."""

    provider: RerankProvider
    candidates: int
    max_document_chars: int


@dataclass(frozen=True, slots=True)
class HydratedChunk:
    """One adapter match the ready manifest confirmed retrieval may serve."""

    entity_id: int
    chunk_key: str
    similarity: float
    # The search_index row this chunk was cut from, read from the manifest's location
    # columns. Several chunks of one row collapse into one candidate on this key.
    source_key: SearchIndexKey


@dataclass(frozen=True, slots=True)
class WindowChunk:
    """One chunk of a candidate window and its position in the adapter's ranking.

    Carries the chunk's key, not its text: ranking needs positions and similarities,
    and only the rows a search returns read chunk text (#1746).
    """

    position: int
    similarity: float
    key: VectorKey


@dataclass(frozen=True, slots=True)
class CandidateWindow:
    """The search rows one vector candidate window resolved to, in adapter order.

    ``similarity_by_key`` holds the best chunk similarity of every row above the
    threshold; ``rows`` holds those of them that exist and that the query's filters
    admit, so a key present in the first and absent from the second was rejected.
    ``chunks_by_key`` keeps every row's chunks in adapter order, each with its
    position in that ranking.
    """

    similarity_by_key: dict[SearchIndexKey, float]
    chunks_by_key: dict[SearchIndexKey, list[WindowChunk]]
    rows: dict[SearchIndexKey, SearchIndexRow]
    chunk_count: int
    # The embedded query, kept so the returned rows' excerpts are ranked against it.
    query_embedding: tuple[float, ...] = ()
    embed_ms: float = 0.0
    vector_query_ms: float = 0.0
    hydrate_ms: float = 0.0

    @property
    def admitted(self) -> int:
        return sum(1 for key in self.similarity_by_key if key in self.rows)

    def position(self, key: SearchIndexKey) -> int:
        """Where a row enters the ranking: the position of its first, best chunk.

        Adapters return matches nearest first, so a row's first chunk is its best.
        """
        return self.chunks_by_key[key][0].position

    def ranked_rows(self, position_bound: int | None = None) -> list[SearchIndexRow]:
        """Admitted rows, best similarity first, as a window of ``position_bound`` sees them.

        A window reaching further down the adapter ranking only appends chunks, so the
        rows that enter before ``position_bound`` and their scores are the same in every
        window that reaches the bound. ``None`` reads the whole window. Rows carry no
        note text here; ``SemanticSearch._materialize`` adds it to the rows returned.
        """
        ranked: list[SearchIndexRow] = []
        for key, similarity in self.similarity_by_key.items():
            row = self.rows.get(key)
            if row is None:
                continue
            chunks = [
                chunk
                for chunk in self.chunks_by_key[key]
                if position_bound is None or chunk.position < position_bound
            ]
            if not chunks:
                continue
            ranked.append(replace(row, score=similarity))

        ranked.sort(key=lambda item: item.score or 0.0, reverse=True)
        return ranked


def _location_values(
    locations: Sequence[tuple[int, str, int]], params: dict[str, Any], *, prefix: str = ""
) -> str:
    """Bind search-row locations as a SQL VALUES list of (project_id, type, id)."""
    rows = []
    for index, (project_id, row_type, row_id) in enumerate(locations):
        params[f"{prefix}loc_project_{index}"] = project_id
        params[f"{prefix}loc_type_{index}"] = row_type
        params[f"{prefix}loc_id_{index}"] = row_id
        # Postgres types untyped binds in a bare VALUES list as text; cast each column.
        rows.append(
            f"(CAST(:{prefix}loc_project_{index} AS INTEGER), "
            f"CAST(:{prefix}loc_type_{index} AS TEXT), "
            f"CAST(:{prefix}loc_id_{index} AS INTEGER))"
        )
    return ", ".join(rows)


def row_key(row: SearchIndexRow) -> SearchIndexKey:
    """The ``(type, id)`` pair that identifies a search row across row types (#982)."""
    assert row.id is not None
    return row.type, row.id


@dataclass(frozen=True, slots=True)
class Fusion:
    """Score-based fusion of one FTS ranking and one vector ranking, keyed on (type, id)."""

    rows_by_key: dict[SearchIndexKey, SearchIndexRow]
    fts_scores: dict[SearchIndexKey, float]
    fts_ranks: dict[SearchIndexKey, int]
    vector_scores: dict[SearchIndexKey, float]
    vector_ranks: dict[SearchIndexKey, int]
    fts_max: float
    # Fused scores, best first; equal scores order by key so the ranking is repeatable.
    ranked: list[tuple[SearchIndexKey, float]]

    def rows(self, entries: Sequence[tuple[SearchIndexKey, float]]) -> list[SearchIndexRow]:
        # FTS-only hits keep the bounded content preview and its truncation metadata.
        # Copying the full note into matched_chunk would bypass that response bound.
        return [replace(self.rows_by_key[key], score=score) for key, score in entries]


def fuse(fts_rows: Sequence[SearchIndexRow], vector_rows: Sequence[SearchIndexRow]) -> Fusion:
    """Fuse with ``max(vec, fts) + FUSION_BONUS * min(vec, fts)``.

    The formula preserves the dominant signal and rewards dual-source agreement. A bare
    row id collides across row types (independent id sequences), so fusion keys on
    (type, id) or distinct rows would merge (#982).
    """
    rows_by_key: dict[SearchIndexKey, SearchIndexRow] = {}

    # FTS scores are normalized to [0, 1] (BM25 is unbounded). Absolute values handle
    # both SQLite (negative bm25) and Postgres (positive ts_rank).
    fts_abs = [abs(row.score or 0.0) for row in fts_rows]
    fts_max = max(fts_abs) if fts_abs else 1.0
    fts_scores: dict[SearchIndexKey, float] = {}
    fts_ranks: dict[SearchIndexKey, int] = {}
    for rank, row in enumerate(fts_rows):
        if row.id is None:
            continue
        key = row_key(row)
        norm = abs(row.score or 0.0) / fts_max if fts_max > 0 else 0.0
        # Gate: FTS scores below threshold contribute zero
        if norm < FTS_GATE_THRESHOLD:
            norm = 0.0
        fts_scores[key] = norm
        fts_ranks.setdefault(key, rank)
        rows_by_key[key] = row

    vector_scores: dict[SearchIndexKey, float] = {}
    vector_ranks: dict[SearchIndexKey, int] = {}
    # Vector rows are fetched by the id their chunk key names, so every one has an id.
    for rank, row in enumerate(vector_rows):
        key = row_key(row)
        # Trigger: no re-normalization by vec_max
        # Why: vector similarity is already calibrated [0, 1]; re-normalizing
        # inflates weak matches when the entire result set is mediocre
        vector_scores[key] = row.score or 0.0
        vector_ranks.setdefault(key, rank)
        rows_by_key[key] = row

    # Output range: [0, 1.3] for dual-source, [0, 1.0] for single-source.
    fused_scores: dict[SearchIndexKey, float] = {}
    for key in fts_scores.keys() | vector_scores.keys():
        v = vector_scores.get(key, 0.0)
        f = fts_scores.get(key, 0.0)
        fused_scores[key] = max(v, f) + FUSION_BONUS * min(v, f)

    ranked = sorted(fused_scores.items(), key=lambda item: (-item[1], item[0]))
    return Fusion(
        rows_by_key=rows_by_key,
        fts_scores=fts_scores,
        fts_ranks=fts_ranks,
        vector_scores=vector_scores,
        vector_ranks=vector_ranks,
        fts_max=fts_max,
        ranked=ranked,
    )


# --- Vector and hybrid retrieval ---


class SemanticSearch:
    """Vector and hybrid retrieval over one scope.

    Constructed only when the semantic stack is present, so every stage reads its
    adapter, provider, and thresholds directly instead of re-checking availability.
    """

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        scope: ProjectScope,
        fts: FtsBackend,
        vector: VectorRetrieval,
        rerank: Reranking | None = None,
    ) -> None:
        self.session_maker = session_maker
        self.scope = scope
        self.fts = fts
        self.vector = vector
        self.rerank = rerank

    # --- Candidate window sizing ---

    def _active_rerank(self, query_text: str) -> Reranking | None:
        """The reranker this query runs: one is configured and there is text to score."""
        return self.rerank if query_text else None

    def _rerank_candidate_limit(self, rerank: Reranking) -> int:
        """Return the fixed chunk window that owns reranker-prefix membership."""
        return max(self.vector.vector_k, rerank.candidates * RERANK_POOL_CHUNK_FANOUT)

    def _candidate_limit(self, limit: int, offset: int, query_text: str) -> int:
        """Size the retrieval candidate *chunk* pool for vector/hybrid search.

        ``candidate_limit`` bounds vector chunks, but many chunks of one large note
        collapse to a single ``(type, id)`` row before reranking, so a chunk count does
        not equal a unique-document count. When reranking is active we over-fetch by
        ``RERANK_POOL_CHUNK_FANOUT`` so a few multi-chunk notes can't starve the rerank
        window below ``reranker_candidates`` unique rows. This is best-effort headroom,
        not a hard guarantee — a single note dominating the entire nearest-neighbour set
        can still yield fewer unique rows (a pathological corpus shape).
        """
        rerank = self._active_rerank(query_text)
        if rerank is None:
            return max(self.vector.vector_k, (limit + offset) * 10)
        # Trigger: the requested window extends beyond the fixed reranked prefix.
        # Why: a bounded prefix alone can under-fill large pages and hide the
        # semantic pagination probe even when more matches exist.
        # Outcome: keep prefix membership fixed while adding chunk headroom only
        # for the untouched tail that this request must return.
        tail_size = max(0, limit + offset - rerank.candidates)
        return self._rerank_candidate_limit(rerank) + tail_size * 10

    def _row_target(self, limit: int, offset: int, query_text: str) -> int:
        """How many admitted *rows* a filtered window must hold to answer this request.

        The page needs ``offset + limit`` rows, and a reranked query also needs its
        whole fixed prefix. This is a row count; ``_candidate_limit`` is a chunk count,
        and a large note owns dozens of chunks, so the two must never be compared.
        """
        rerank = self._active_rerank(query_text)
        page_end = offset + limit
        if rerank is None:
            return page_end
        return max(rerank.candidates, page_end)

    # --- Vector nearest neighbours through the ready manifest ---

    @logfire.instrument("search.vector_query", extract_args=False)
    async def _run_vector_query(
        self,
        session: AsyncSession,
        query_embedding: list[float],
        candidate_limit: int,
        *,
        admission: VectorAdmission | None = None,
        trace: SearchTraceCollector | None = None,
    ) -> list[HydratedChunk]:
        """Query the configured adapter and hydrate only live, ready manifest rows.

        ``admission`` restricts a built-in index to the chunks a filtered query admits.
        """
        if trace is not None:
            trace.vector = build_vector_stage(
                candidate_limit=candidate_limit,
                adapter_match_count=0,
                hydrated_count=0,
            )
        if candidate_limit <= 0:
            return []

        if not self.vector.external:
            matches = await self.vector.index.search(
                query_embedding,
                limit=candidate_limit,
                projects=self.scope,
                admission=admission,
            )
            if trace is not None:
                trace.readiness = await read_manifest_readiness(
                    session,
                    self.scope,
                    self.vector.index_name,
                    self.vector.embedding_model,
                )
            return await self._hydrate_vector_matches(session, matches, trace=trace)

        if admission is not None:  # pragma: no cover - the reader never builds one
            raise ValueError("An external vector index cannot evaluate a SQL admission.")
        scan_limit = min(candidate_limit, VECTOR_FILTER_SCAN_LIMIT)
        while True:
            matches = await self.vector.index.search(
                query_embedding, limit=scan_limit, projects=self.scope
            )
            if trace is not None and trace.readiness is None:
                trace.readiness = await read_manifest_readiness(
                    session,
                    self.scope,
                    self.vector.index_name,
                    self.vector.embedding_model,
                )
            hydrated = await self._hydrate_vector_matches(session, matches, trace=trace)
            if (
                len(hydrated) >= candidate_limit
                or len(matches) < scan_limit
                or scan_limit >= VECTOR_FILTER_SCAN_LIMIT
            ):
                returned = hydrated[:candidate_limit]
                # Trigger: the expanded stale-hit rescan hydrated more chunks than the
                # candidate window the search consumes.
                # Why: chunks beyond the window never enter thresholding, fusion, or
                # reranking — tracing them would invent candidates this execution
                # never considered.
                # Outcome: the traced stage is trimmed to the returned window.
                if trace is not None and trace.vector is not None and len(hydrated) > len(returned):
                    # Two owners can share one parseable chunk_key (manifest uniqueness
                    # includes entity_id), so window membership matches by owner too.
                    returned_chunk_keys = {(chunk.entity_id, chunk.chunk_key) for chunk in returned}
                    trimmed: dict[SearchIndexKey, list[tuple[str, float, int | None]]] = {}
                    for chunk_match in trace.vector.chunk_matches:
                        if (chunk_match.entity_id, chunk_match.chunk_key) in returned_chunk_keys:
                            trimmed.setdefault(chunk_match.key, []).append(
                                (
                                    chunk_match.chunk_key,
                                    chunk_match.similarity,
                                    chunk_match.entity_id,
                                )
                            )
                    # hydrated_count keeps full-scan scope so the vector stage's
                    # dropped count matches its hydration-drop list; the flattener
                    # reports the window truncation as its own candidate_window stage.
                    trace.vector = build_vector_stage(
                        previous=trace.vector,
                        chunk_matches=trimmed,
                    )
                return returned

            # Trigger: stale, pending, or wrong-model adapter hits consumed the
            # requested top-k before manifest hydration.
            # Why: returning early lets stale extension data crowd every live
            # result out of an otherwise valid semantic search.
            # Outcome: retry from the same ranked prefix with bounded geometric
            # overfetch until enough live rows survive or the adapter is exhausted.
            scan_limit = min(scan_limit * 2, VECTOR_FILTER_SCAN_LIMIT)

    @logfire.instrument("search.vector_manifest_hydration", extract_args=False)
    async def _hydrate_vector_matches(
        self,
        session: AsyncSession,
        matches: list[VectorMatch],
        *,
        trace: SearchTraceCollector | None = None,
    ) -> list[HydratedChunk]:
        """Resolve adapter matches through the authoritative ready manifest."""
        if not matches:
            return []

        chunks_by_key: dict[VectorKey, SearchIndexKey] = {}
        for batch_start in range(0, len(matches), VECTOR_HYDRATION_BATCH_SIZE):
            batch = matches[batch_start : batch_start + VECTOR_HYDRATION_BATCH_SIZE]
            params: dict[str, Any] = {
                "vector_index": self.vector.index_name,
                "embedding_model": self.vector.embedding_model,
            }
            manifest_predicate = current_vector_manifest_predicate(self.scope, params)
            predicates: list[str] = []
            for index, match in enumerate(batch):
                params[f"entity_id_{index}"] = match.key.entity_id
                params[f"chunk_key_{index}"] = match.key.chunk_key
                predicates.append(
                    f"(entity_id = :entity_id_{index} AND chunk_key = :chunk_key_{index})"
                )

            # Constraint: adapters may return thousands of candidates for deep pages.
            # PostgreSQL and SQLite both cap bind parameters, so hydrate in fixed-size
            # batches while retaining the adapter's original ranking in the final list.
            result = await session.execute(
                text(
                    "SELECT entity_id, chunk_key, source_type, source_row_id "
                    "FROM search_vector_chunks "
                    "WHERE " + manifest_predicate + " "
                    "AND (" + " OR ".join(predicates) + ")"
                ),
                params,
            )
            chunks_by_key.update(
                {
                    VectorKey(
                        entity_id=int(row["entity_id"]),
                        chunk_key=str(row["chunk_key"]),
                    ): (str(row["source_type"]), int(row["source_row_id"]))
                    for row in result.mappings().all()
                }
            )
        hydrated = [
            HydratedChunk(
                entity_id=match.key.entity_id,
                chunk_key=match.key.chunk_key,
                similarity=match.similarity,
                source_key=chunks_by_key[match.key],
            )
            for match in matches
            if match.key in chunks_by_key
        ]
        if trace is not None:
            dropped_keys = [
                HydrationDropKey(
                    entity_id=match.key.entity_id,
                    chunk_key=match.key.chunk_key,
                    similarity=match.similarity,
                    configured_index=self.vector.index_name,
                    configured_model=self.vector.embedding_model,
                )
                for match in matches
                if match.key not in chunks_by_key
            ]
            drops = await classify_hydration_drops(session, self.scope, dropped_keys)
            chunk_matches: dict[SearchIndexKey, list[tuple[str, float, int | None]]] = {}
            for chunk in hydrated:
                chunk_matches.setdefault(chunk.source_key, []).append(
                    (chunk.chunk_key, chunk.similarity, chunk.entity_id)
                )
            trace.vector = build_vector_stage(
                previous=trace.vector,
                adapter_match_count=len(matches),
                hydrated_count=len(hydrated),
                drops=drops,
                chunk_matches=chunk_matches,
            )
        return hydrated

    # --- Candidate rows and structured filters ---

    @logfire.instrument("search.fetch_candidate_rows", extract_args=False)
    async def _fetch_search_index_rows_by_ids(
        self, row_ids: list[int]
    ) -> dict[SearchIndexKey, SearchIndexRow]:
        """Fetch search_index rows by id, keyed by (type, id) to disambiguate types.

        A bare id can match one row per type (independent id sequences), so the
        result must carry every matching row rather than letting one clobber another.
        """
        if not row_ids:
            return {}
        placeholders = ",".join(f":id_{idx}" for idx in range(len(row_ids)))
        params: dict[str, Any] = {f"id_{idx}": rid for idx, rid in enumerate(row_ids)}
        scope_predicate = self.scope.predicate("project_id", params)
        sql = f"""
            SELECT
                project_id, id, title, permalink, file_path, type, metadata,
                from_id, to_id, relation_type, entity_id,
                {snippet_length_columns("search_index")},
                category, created_at, updated_at, 0 as score
            FROM search_index
            WHERE {scope_predicate}
              AND id IN ({placeholders})
        """
        result: dict[SearchIndexKey, SearchIndexRow] = {}
        async with db.scoped_session(self.session_maker) as session:
            row_result = await session.execute(text(sql), params)
            for row in row_result.fetchall():
                search_row = SearchIndexRow.from_mapping(row._asdict())
                result[(search_row.type, search_row.id)] = search_row
        return result

    @logfire.instrument("search.filter_candidates", extract_args=False)
    async def _filter_candidate_keys(
        self,
        candidate_keys: Sequence[SearchIndexKey],
        query: PreparedSearchQuery,
    ) -> set[SearchIndexKey]:
        """Return which of ``candidate_keys`` the query's structured filters admit.

        Vector retrieval scores embeddings and cannot evaluate a structured filter, so
        the surviving candidates are decided by an FTS-mode pass carrying every filter.
        Asking that pass for a *page of the filter's whole match set* and intersecting
        client-side silently lost any candidate that sorted past the page (#1431); asking
        it about the candidates themselves cannot, because the answer is bounded by the
        question.

        The candidate list is split at the shared bind-parameter bound, so a deep page
        whose candidate pool runs to thousands of rows costs a few small indexed lookups
        instead of one unbounded scan.
        """
        filter_query = replace(query, search_text=None, retrieval_mode=SearchRetrievalMode.FTS)
        allowed_keys: set[SearchIndexKey] = set()
        for batch_start in range(0, len(candidate_keys), VECTOR_HYDRATION_BATCH_SIZE):
            batch = candidate_keys[batch_start : batch_start + VECTOR_HYDRATION_BATCH_SIZE]
            allowed_keys.update(await self.fts.admitted_keys(self.scope, filter_query, batch))
        return allowed_keys

    # --- Reranking ---

    def _prefix_rows(
        self,
        window: CandidateWindow,
        count: int,
        query: PreparedSearchQuery,
        rerank: Reranking,
    ) -> tuple[list[SearchIndexRow], int]:
        """The first ``count`` vector rows exactly as the fixed rerank window sees them.

        Returns the rows and the position bound they were read through, which also
        bounds the chunks an external index's excerpts may quote for them.

        A request whose page reaches past the reranked prefix retrieves a larger
        window, but its prefix must match the one every shallower page reranked, or
        pages duplicate and skip rows. The fixed window reads the adapter ranking up
        to ``_rerank_candidate_limit`` chunks, so the prefix is read from this window
        through that same position bound instead of from a second retrieval (#1557).
        """
        position_bound = self._rerank_candidate_limit(rerank)
        # Trigger: the query carries structured filters and an external index, which
        #     filters in the reader (a built-in index admits only matching chunks, so
        #     its positions already count admitted chunks and need no extension).
        # Why: the fixed window then keeps widening past the bound until it admits
        #     enough rows, so its leading admitted rows can enter the ranking beyond
        #     it. Every window of this request admits at least as many rows, so those
        #     leading rows are the same in all of them.
        # Outcome: the bound extends to cover the leading rows; passages past it
        #     stay out of the prefix, since a smaller window may not have read them.
        if query.has_filters and self.vector.external:
            leading = window.ranked_rows()[:count]
            if leading:
                last_position = max(window.position(row_key(row)) for row in leading)
                position_bound = max(position_bound, last_position + 1)
        return window.ranked_rows(position_bound)[:count], position_bound

    async def _rerank_and_paginate(
        self,
        rerank: Reranking,
        query_text: str,
        pool: list[SearchIndexRow],
        tail: list[SearchIndexRow],
        *,
        offset: int,
        limit: int,
        trace: SearchTraceCollector | None = None,
    ) -> list[SearchIndexRow]:
        """Rerank the fixed prefix, then return the requested ``[offset:offset+limit]`` page.

        Trigger: a reranker is configured and there is a real query.
        Why: bi-encoder/FTS ranking lands the gold document in the top-N but often
        just below the top-k cutoff (#950); a cross-encoder that reads query and
        document together recovers those near-misses.
        Outcome: the prefix (at most ``reranker_candidates`` rows) is reordered by
        reranker relevance (which replaces ``score``); the tail follows it, and the
        requested page is sliced from the combined list.

        The caller owns prefix membership: it is the same on every page of a query,
        and ``tail`` holds only rows outside it. Every non-empty page rescores that
        prefix before slicing so the untouched tail can be demoted onto the
        reranker's public ``[0, 1]`` scale.
        """
        page_end = offset + limit
        ordered_rows = pool + tail

        # Skip only when there is no prefix to calibrate or the requested page is
        # empty. Even a singleton prefix or a wholly-tail page needs the prefix's
        # relevance floor so raw hybrid scores cannot leak into cross-project sorting.
        if not pool or offset >= len(ordered_rows):
            return ordered_rows[offset:page_end]

        pre_rerank_scores = None
        if trace is not None:
            pre_rerank_scores = {(row.type, row.id): row.score or 0.0 for row in ordered_rows}
        documents = [rerank_document_text(row, rerank.max_document_chars) for row in pool]
        # A transient provider failure must surface instead of switching this page
        # back to retrieval order. A prior page may already have returned reranked
        # order, so degrading here can duplicate one result and omit another.
        rerank_start = time.perf_counter() if trace is not None else None
        with logfire.span(
            "search.rerank",
            candidate_count=len(pool),
            document_chars=sum(map(len, documents)),
        ):
            scores = validate_rerank_scores(
                await rerank.provider.rerank(query_text, documents),
                len(pool),
            )

        order = sorted(range(len(pool)), key=lambda i: scores[i], reverse=True)
        reranked = [replace(pool[i], score=scores[i]) for i in order]
        logger.debug(
            "Reranked candidates: pool={pool} model={model}",
            pool=len(pool),
            model=rerank.provider.model_name,
        )
        tail_floor = reranked[-1].score or 0.0
        demoted_tail = demote_tail(tail, floor=tail_floor)
        reranked_rows = reranked + demoted_tail
        if trace is not None:
            assert pre_rerank_scores is not None and rerank_start is not None
            trace.rerank = build_rerank_stage(
                provider_model=rerank.provider.model_name,
                reranker_candidates=rerank.candidates,
                pre_rerank_scores=pre_rerank_scores,
                pool_keys=[(row.type, row.id) for row in pool],
                rerank_scores={
                    (pool[index].type, pool[index].id): score for index, score in enumerate(scores)
                },
                post_rerank_rows=[((row.type, row.id), row.score or 0.0) for row in reranked_rows],
                demoted_scores={(row.type, row.id): row.score or 0.0 for row in demoted_tail},
                tail_floor=tail_floor,
                rerank_ms=(time.perf_counter() - rerank_start) * 1000,
            )
        return reranked_rows[offset:page_end]

    # --- Text for the rows a search returns ---

    async def _materialize(
        self,
        rows: Sequence[SearchIndexRow],
        window: CandidateWindow,
        *,
        vector_keys: Collection[SearchIndexKey],
        position_bound: int | None = None,
    ) -> list[SearchIndexRow]:
        """Attach note text to the rows a search returns (#1746).

        Ranking reads ids, scores and stored lengths only. Once the returned rows are
        known, each gets its capped ``content_snippet``, and each row the vector leg
        matched (``vector_keys``) gets ``matched_chunk_text``: the whole note when it is
        small, otherwise the note's ``TOP_CHUNKS_PER_RESULT`` chunks nearest the query.
        ``position_bound`` limits an external index's excerpts to the chunks the fixed
        rerank window read, so a reranked prefix quotes the same passages on every page.
        """
        if not rows:
            return []
        texts = await self._fetch_row_texts([row_key(row) for row in rows])
        rows = [
            replace(
                row,
                content_snippet=texts[row_key(row)][0],
                content_snippet_length=texts[row_key(row)][1],
            )
            if row_key(row) in texts
            else row
            for row in rows
        ]
        # Small notes quote themselves whole, so the answer is always present.
        excerpt_rows = [
            row
            for row in rows
            if row_key(row) in vector_keys
            and not (row.content_snippet and (row.content_length or 0) <= SMALL_NOTE_CONTENT_LIMIT)
        ]
        excerpt_keys = await self._excerpt_chunk_keys(excerpt_rows, window, position_bound)
        chunk_texts = await self._fetch_chunk_texts(
            [key for keys in excerpt_keys.values() for key in keys]
        )

        materialized: list[SearchIndexRow] = []
        for row in rows:
            key = row_key(row)
            if key not in vector_keys:
                materialized.append(row)
            elif key in excerpt_keys:
                passages = [
                    chunk_texts[chunk] for chunk in excerpt_keys[key] if chunk in chunk_texts
                ]
                materialized.append(replace(row, matched_chunk_text="\n---\n".join(passages)))
            else:
                materialized.append(replace(row, matched_chunk_text=row.content_snippet))
        return materialized

    async def _materialize_unread(
        self,
        rows: Sequence[SearchIndexRow],
        read_keys: Collection[SearchIndexKey],
        window: CandidateWindow,
        *,
        vector_keys: Collection[SearchIndexKey],
    ) -> list[SearchIndexRow]:
        """Materialize the rows not in ``read_keys`` (a reranked prefix already is)."""
        unread = await self._materialize(
            [row for row in rows if row_key(row) not in read_keys],
            window,
            vector_keys=vector_keys,
        )
        by_key = {row_key(row): row for row in unread}
        return [by_key.get(row_key(row), row) for row in rows]

    async def _fetch_row_texts(
        self, keys: Sequence[SearchIndexKey]
    ) -> dict[SearchIndexKey, tuple[str | None, int | None]]:
        """The capped content_snippet and stored length of each search row in ``keys``."""
        wanted = set(keys)
        row_ids = list(dict.fromkeys(row_id for _, row_id in keys))
        texts: dict[SearchIndexKey, tuple[str | None, int | None]] = {}
        async with db.scoped_session(self.session_maker) as session:
            for batch_start in range(0, len(row_ids), VECTOR_HYDRATION_BATCH_SIZE):
                batch = row_ids[batch_start : batch_start + VECTOR_HYDRATION_BATCH_SIZE]
                params: dict[str, Any] = {
                    f"id_{index}": row_id for index, row_id in enumerate(batch)
                }
                scope_predicate = self.scope.predicate("project_id", params)
                result = await session.execute(
                    text(
                        f"SELECT type, id, {capped_snippet_columns('search_index')} "
                        f"FROM search_index WHERE {scope_predicate} "
                        f"AND id IN ({', '.join(f':id_{index}' for index in range(len(batch)))})"
                    ),
                    params,
                )
                for row in result.mappings().all():
                    key = (str(row["type"]), int(row["id"]))
                    if key in wanted:
                        texts[key] = (row["content_snippet"], row["content_snippet_length"])
        return texts

    async def _excerpt_chunk_keys(
        self,
        rows: Sequence[SearchIndexRow],
        window: CandidateWindow,
        position_bound: int | None,
    ) -> dict[SearchIndexKey, list[VectorKey]]:
        """Each row's chunks nearest the query, best first, at most TOP_CHUNKS_PER_RESULT.

        A built-in index ranks every chunk of each row against the query, so an excerpt
        never depends on how far the candidate window reached. An external index cannot
        be asked about chosen chunks; its rows quote the window chunks it returned.
        """
        if not rows:
            return {}
        if self.vector.external:
            excerpts: dict[SearchIndexKey, list[VectorKey]] = {}
            for row in rows:
                chunks = [
                    chunk
                    for chunk in window.chunks_by_key.get(row_key(row), [])
                    if position_bound is None or chunk.position < position_bound
                ]
                chunks.sort(key=lambda chunk: chunk.similarity, reverse=True)
                excerpts[row_key(row)] = [chunk.key for chunk in chunks[:TOP_CHUNKS_PER_RESULT]]
            return excerpts

        # --- Built-in index: rank each row's own chunks ---
        locations = [(row.project_id, row.type, row.id) for row in rows]
        chunk_rows = await self._ready_chunks_of(locations)
        ranked: dict[SearchIndexKey, list[tuple[float, VectorKey]]] = {}
        batch: list[tuple[int, str, int]] = []
        batch_chunks = 0
        chunk_counts: dict[tuple[int, str, int], int] = {}
        for location in chunk_rows.values():
            chunk_counts[location] = chunk_counts.get(location, 0) + 1
        for location in locations:
            count = chunk_counts.get(location, 0)
            if count == 0:
                continue
            if batch and batch_chunks + count > EXCERPT_RANKING_CHUNK_BUDGET:
                await self._rank_chunks_of(batch, batch_chunks, window, chunk_rows, ranked)
                batch, batch_chunks = [], 0
            batch.append(location)
            batch_chunks += count
        if batch:
            await self._rank_chunks_of(batch, batch_chunks, window, chunk_rows, ranked)
        return {
            key: [
                chunk
                for _, chunk in sorted(
                    scored, key=lambda item: (-item[0], item[1].entity_id, item[1].chunk_key)
                )[:TOP_CHUNKS_PER_RESULT]
            ]
            for key, scored in ranked.items()
        }

    async def _ready_chunks_of(
        self, locations: Sequence[tuple[int, str, int]]
    ) -> dict[VectorKey, tuple[int, str, int]]:
        """Every ready chunk cut from the given search rows, keyed to its row location."""
        params: dict[str, Any] = {
            "vector_index": self.vector.index_name,
            "embedding_model": self.vector.embedding_model,
        }
        manifest_predicate = current_vector_manifest_predicate(self.scope, params)
        values = _location_values(locations, params)
        async with db.scoped_session(self.session_maker) as session:
            result = await session.execute(
                text(
                    "SELECT entity_id, chunk_key, project_id, source_type, source_row_id "
                    "FROM search_vector_chunks WHERE " + manifest_predicate + " "
                    f"AND (project_id, source_type, source_row_id) IN (VALUES {values})"
                ),
                params,
            )
            return {
                VectorKey(entity_id=int(row["entity_id"]), chunk_key=str(row["chunk_key"])): (
                    int(row["project_id"]),
                    str(row["source_type"]),
                    int(row["source_row_id"]),
                )
                for row in result.mappings().all()
            }

    async def _rank_chunks_of(
        self,
        locations: Sequence[tuple[int, str, int]],
        chunk_count: int,
        window: CandidateWindow,
        chunk_rows: dict[VectorKey, tuple[int, str, int]],
        ranked: dict[SearchIndexKey, list[tuple[float, VectorKey]]],
    ) -> None:
        """Rank every chunk of ``locations`` against the query through the index."""
        params: dict[str, object] = {}
        values = _location_values(locations, params, prefix="excerpt_")
        admission = VectorAdmission(
            predicate=f"(c.project_id, c.source_type, c.source_row_id) IN (VALUES {values})",
            params=params,
        )
        matches = await self.vector.index.search(
            list(window.query_embedding),
            limit=chunk_count,
            projects=self.scope,
            admission=admission,
        )
        for match in matches:
            location = chunk_rows.get(match.key)
            if location is not None:
                ranked.setdefault((location[1], location[2]), []).append(
                    (match.similarity, match.key)
                )

    async def _fetch_chunk_texts(self, keys: Sequence[VectorKey]) -> dict[VectorKey, str]:
        """The text of each chunk in ``keys``, read from the manifest."""
        chunk_keys = list(dict.fromkeys(keys))
        texts: dict[VectorKey, str] = {}
        async with db.scoped_session(self.session_maker) as session:
            for batch_start in range(0, len(chunk_keys), VECTOR_HYDRATION_BATCH_SIZE):
                batch = chunk_keys[batch_start : batch_start + VECTOR_HYDRATION_BATCH_SIZE]
                params: dict[str, Any] = {}
                scope_predicate = self.scope.predicate("project_id", params)
                predicates = []
                for index, key in enumerate(batch):
                    params[f"entity_id_{index}"] = key.entity_id
                    params[f"chunk_key_{index}"] = key.chunk_key
                    predicates.append(
                        f"(entity_id = :entity_id_{index} AND chunk_key = :chunk_key_{index})"
                    )
                result = await session.execute(
                    text(
                        "SELECT entity_id, chunk_key, chunk_text FROM search_vector_chunks "
                        f"WHERE {scope_predicate} AND (" + " OR ".join(predicates) + ")"
                    ),
                    params,
                )
                for row in result.mappings().all():
                    texts[
                        VectorKey(entity_id=int(row["entity_id"]), chunk_key=str(row["chunk_key"]))
                    ] = str(row["chunk_text"])
        return texts

    # --- Vector-only retrieval ---

    async def vector_only(
        self,
        query: PreparedSearchQuery,
        *,
        limit: int,
        offset: int,
        trace: SearchTraceCollector | None = None,
    ) -> list[SearchIndexRow]:
        """Run vector-only search returning chunk-level results.

        Returns individual search_index rows (entities, observations, relations)
        ranked by vector similarity. Each observation or relation is a first-class
        result, not collapsed into its parent entity.
        """
        # An empty scope admits no rows; embedding the query would buy nothing.
        if self.scope.is_empty:
            return []
        query_text = (query.search_text or "").strip()
        rerank = self._active_rerank(query_text)
        candidate_limit = self._candidate_limit(limit, offset, query_text)
        row_target = self._row_target(limit, offset, query_text)
        query_start = time.perf_counter()
        window = await self._vector_window(query, candidate_limit, row_target, trace=trace)
        ranked_rows = window.ranked_rows()

        vector_keys = window.rows.keys()
        if rerank is None:
            output = await self._materialize(
                ranked_rows[offset : offset + limit], window, vector_keys=vector_keys
            )
        else:
            # One window feeds both halves: the fixed prefix is read from it at the
            # fixed window's bound, and the tail is every other row in similarity order.
            prefix, prefix_bound = self._prefix_rows(window, rerank.candidates, query, rerank)
            # The reranker reads the prefix's text, so it is materialized first.
            prefix = await self._materialize(
                prefix, window, vector_keys=vector_keys, position_bound=prefix_bound
            )
            prefix_keys = {row_key(row) for row in prefix}
            tail = [row for row in ranked_rows if row_key(row) not in prefix_keys]
            output = await self._rerank_and_paginate(
                rerank, query_text, prefix, tail, offset=offset, limit=limit, trace=trace
            )
            output = await self._materialize_unread(
                output, prefix_keys, window, vector_keys=vector_keys
            )

        # Vector latency owns the optional rerank stage too. Logging before the
        # awaited provider call hides the feature's dominant cost and can suppress
        # the slow-query warning entirely.
        total_ms = (time.perf_counter() - query_start) * 1000
        if total_ms > 2000:
            logger.warning(
                "[SEMANTIC_SLOW_QUERY] Semantic query timing: scope={scope} "
                "retrieval_mode={retrieval_mode} query_length={query_length} "
                "candidate_limit={candidate_limit} vector_row_count={vector_row_count} "
                "embed_ms={embed_ms:.2f} vector_query_ms={vector_query_ms:.2f} "
                "hydrate_ms={hydrate_ms:.2f} total_ms={total_ms:.2f}",
                scope=self.scope.project_ids,
                retrieval_mode="vector",
                query_length=len(query_text),
                candidate_limit=candidate_limit,
                vector_row_count=window.chunk_count,
                embed_ms=window.embed_ms,
                vector_query_ms=window.vector_query_ms,
                hydrate_ms=window.hydrate_ms,
                total_ms=total_ms,
            )
        return output

    async def _vector_window(
        self,
        query: PreparedSearchQuery,
        candidate_limit: int,
        row_target: int,
        *,
        trace: SearchTraceCollector | None = None,
    ) -> CandidateWindow:
        """Embed the query once and resolve its candidate window of ``candidate_limit`` chunks.

        ``row_target`` is how many admitted rows a filtered window must reach before it
        stops widening; see ``_candidate_window``.
        """
        query_text = (query.search_text or "").strip()
        embed_start = time.perf_counter()
        with logfire.span("search.embed_query", query_chars=len(query_text)):
            query_embedding = await self.vector.embedding_provider.embed_query(query_text)
        embed_ms = (time.perf_counter() - embed_start) * 1000
        # Per-query min_similarity overrides the configured default.
        effective_min_similarity = (
            query.min_similarity if query.min_similarity is not None else self.vector.min_similarity
        )

        window = await self._candidate_window(
            query,
            query_embedding,
            candidate_limit,
            row_target,
            min_similarity=effective_min_similarity,
            trace=trace,
        )

        if trace is not None:
            trace.vector = build_vector_stage(
                previous=trace.vector,
                effective_min_similarity=effective_min_similarity,
                min_similarity_source=("query" if query.min_similarity is not None else "config"),
                embed_ms=embed_ms,
                vector_query_ms=window.vector_query_ms,
            )
        return replace(window, embed_ms=embed_ms, query_embedding=tuple(query_embedding))

    async def _vector_admission(self, query: PreparedSearchQuery) -> VectorAdmission | None:
        """The query's structured filters as a predicate a built-in index evaluates.

        Row types are a column of the chunk manifest, so the default entity filter
        costs one comparison per chunk. Every other filter is the same SQL full-text
        search applies, as a subquery naming the search rows it admits.
        """
        if not query.has_filters:
            return None
        params: dict[str, object] = {}
        predicates: list[str] = []
        if query.search_item_types:
            type_binds = []
            for index, item_type in enumerate(query.search_item_types):
                params[f"admit_type_{index}"] = item_type.value
                type_binds.append(f":admit_type_{index}")
            predicates.append(f"c.source_type IN ({', '.join(type_binds)})")
        row_query = replace(
            query,
            search_text=None,
            search_item_types=None,
            retrieval_mode=SearchRetrievalMode.FTS,
        )
        if row_query.has_filters:
            row_sql, row_params = await self.fts.row_filter(self.scope, row_query)
            row_sql, row_params = prefixed_binds(row_sql, row_params, "admit_")
            params.update(row_params)
            predicates.append(f"(c.project_id, c.source_type, c.source_row_id) IN ({row_sql})")
        return VectorAdmission(predicate=" AND ".join(predicates), params=params)

    async def _candidate_window(
        self,
        query: PreparedSearchQuery,
        query_embedding: list[float],
        candidate_limit: int,
        row_target: int,
        *,
        min_similarity: float,
        trace: SearchTraceCollector | None = None,
    ) -> CandidateWindow:
        """Resolve the nearest chunks to admitted search rows, widening past rejections.

        The first read takes ``candidate_limit`` chunks, the same window an unfiltered
        query reads, so a filtered ranking is the unfiltered one restricted to the
        filter.

        Trigger: the query carries structured filters the adapter cannot evaluate, and
            that first window admitted fewer than ``row_target`` rows.
        Why: the adapter ranks by similarity alone, so a window taken straight from
            its ranking can hold few admitted rows while more sit just past it, and a
            page built from that window comes up short although matches exist.
        Outcome: the window is re-read with a bounded geometric overfetch until it
            holds ``row_target`` admitted rows, the ranking is exhausted, or its tail
            has fallen below the similarity threshold, past which nothing further can
            qualify. A query without filters resolves its window once.

        ``row_target`` counts rows, not chunks (#1746). Comparing admitted rows to the
        chunk budget could never succeed for notes cut into dozens of chunks, so nearly
        every filtered search (and every default MCP search, which filters to entities)
        widened to the scan limit.

        A built-in index evaluates the filters itself (``_vector_admission``): its one
        read is already the exact top ``candidate_limit`` of the admitted chunks, so it
        never widens and no filter pass runs. Only an external index, which cannot see
        the SQL search rows, filters its matches here and widens.
        """
        # Trigger: the vectors live in the SQL database (sqlite-vec, pgvector).
        # Why: the index can rank only admitted chunks; filtering a similarity-only
        #     ranking afterwards forced repeated, ever wider reads to fill one page.
        # Outcome: one admitted read. External indexes keep the filter-and-widen path.
        admission = None if self.vector.external else await self._vector_admission(query)
        filter_in_reader = query.has_filters and admission is None
        scan_limit = candidate_limit
        scanned = -1
        rounds = 0
        vector_query_ms = 0.0
        hydrate_ms = 0.0
        while True:
            rounds += 1
            vector_query_start = time.perf_counter()
            # Constraint: vector adapters may open their own session, while the SQLite
            # test/runtime pool can contain only one connection. A plain AsyncSession
            # defers checkout until hydration runs after adapter search has released it.
            async with self.session_maker() as session:
                chunks = await self._run_vector_query(
                    session, query_embedding, scan_limit, admission=admission, trace=trace
                )
            vector_query_ms += (time.perf_counter() - vector_query_start) * 1000
            hydrate_start = time.perf_counter()
            window = await self._resolve_rows(
                chunks, query, min_similarity, filter_rows=filter_in_reader, trace=trace
            )
            hydrate_ms += (time.perf_counter() - hydrate_start) * 1000

            exhausted = (
                len(chunks) < scan_limit
                or len(chunks) <= scanned
                or scan_limit >= VECTOR_FILTER_SCAN_LIMIT
            )
            tail_below_threshold = bool(chunks) and chunks[-1].similarity < min_similarity
            if (
                not filter_in_reader
                or window.admitted >= row_target
                or exhausted
                or tail_below_threshold
            ):
                logfire.info(
                    "search.vector_window",
                    rounds=rounds,
                    chunks_scanned=len(chunks),
                    candidate_limit=candidate_limit,
                    row_target=row_target,
                    rows_admitted=window.admitted,
                    has_filters=query.has_filters,
                    filtered_in_index=admission is not None,
                )
                return replace(window, vector_query_ms=vector_query_ms, hydrate_ms=hydrate_ms)
            scanned = len(chunks)
            scan_limit = min(scan_limit * 2, VECTOR_FILTER_SCAN_LIMIT)

    async def _resolve_rows(
        self,
        chunks: list[HydratedChunk],
        query: PreparedSearchQuery,
        min_similarity: float,
        *,
        filter_rows: bool,
        trace: SearchTraceCollector | None = None,
    ) -> CandidateWindow:
        """Turn ranked chunks into the search rows above threshold that the filters admit.

        ``filter_rows`` runs the structured filters over the rows here; it is False when
        the index already admitted only matching chunks.
        """
        # Build per-search_index_row similarity scores from chunk-level results.
        # Keep the row type in the key because different row types can share the same
        # numeric id (#982). Track the best similarity per row (for ranking) and all
        # chunks (for context).
        similarity_by_key: dict[SearchIndexKey, float] = {}
        chunks_by_key: dict[SearchIndexKey, list[WindowChunk]] = {}
        for position, chunk in enumerate(chunks):
            si_key = chunk.source_key
            current = similarity_by_key.get(si_key)
            if current is None or chunk.similarity > current:
                similarity_by_key[si_key] = chunk.similarity
            chunks_by_key.setdefault(si_key, []).append(
                WindowChunk(
                    position=position,
                    similarity=chunk.similarity,
                    key=VectorKey(entity_id=chunk.entity_id, chunk_key=chunk.chunk_key),
                )
            )

        # Filter out results below the minimum similarity threshold.
        if min_similarity > 0.0:
            if trace is not None:
                threshold_rejections = tuple(
                    BelowThreshold(key=key, similarity=value, threshold=min_similarity)
                    for key, value in similarity_by_key.items()
                    if value < min_similarity
                )
                trace.vector = build_vector_stage(
                    previous=trace.vector,
                    threshold_rejections=threshold_rejections,
                )
            similarity_by_key = {k: v for k, v in similarity_by_key.items() if v >= min_similarity}
        if not similarity_by_key:
            return CandidateWindow(similarity_by_key, chunks_by_key, {}, len(chunks))

        # Fetch the actual search_index rows. Colliding (type, id) keys share one
        # bare id, so deduplicate while preserving first-seen order.
        si_ids = list(dict.fromkeys(si_id for _, si_id in similarity_by_key))
        search_index_rows = await self._fetch_search_index_rows_by_ids(si_ids)
        if trace is not None:
            trace.vector = build_vector_stage(
                previous=trace.vector,
                missing_search_rows=tuple(
                    MissingSearchRow(key=key)
                    for key in similarity_by_key
                    if key not in search_index_rows
                ),
            )

        if filter_rows:
            allowed_keys = await self._filter_candidate_keys(list(search_index_rows), query)
            if trace is not None:
                trace.vector = build_vector_stage(
                    previous=trace.vector,
                    filter_rejections=tuple(
                        FilteredOut(key=key) for key in search_index_rows if key not in allowed_keys
                    ),
                )
            search_index_rows = {k: v for k, v in search_index_rows.items() if k in allowed_keys}
        return CandidateWindow(similarity_by_key, chunks_by_key, search_index_rows, len(chunks))

    # --- Hybrid score-based fusion ---

    async def hybrid(
        self,
        query: PreparedSearchQuery,
        *,
        limit: int,
        offset: int,
        trace: SearchTraceCollector | None = None,
    ) -> list[SearchIndexRow]:
        """Fuse FTS and vector results using score-based fusion (see ``fuse``)."""
        if self.scope.is_empty:
            return []
        query_text = (query.search_text or "").strip()
        rerank = self._active_rerank(query_text)
        query_start = time.perf_counter()
        candidate_limit = self._candidate_limit(limit, offset, query_text)
        fts_start = time.perf_counter()
        # allow_relaxed: question-form queries rarely AND-match, and a dead FTS
        # branch silently degrades hybrid to vector-only ranking. Fusion plus
        # bm25 keep relaxed lexical candidates from dominating precision.
        with logfire.span("search.fts", candidate_limit=candidate_limit) as fts_span:
            fts_results = await self.fts.search(
                self.scope,
                replace(query, retrieval_mode=SearchRetrievalMode.FTS),
                limit=candidate_limit,
                offset=0,
                allow_relaxed=True,
                trace=trace,
                # Fusion reads scores; the returned rows' text is fetched afterwards.
                with_text=False,
            )
            fts_span.set_attribute("result_count", len(fts_results))
        fts_ms = (time.perf_counter() - fts_start) * 1000
        vector_start = time.perf_counter()
        # Trigger: reranking owns a bounded candidate window shared by both legs.
        # Why: the disabled path historically expands the vector leg again to
        # preserve recall when many vector chunks collapse into a few search rows.
        # Outcome: avoid double expansion only when reranking is actually active.
        vector_candidate_limit = (
            candidate_limit
            if rerank is not None
            else self._candidate_limit(candidate_limit, 0, query_text)
        )
        window = await self._vector_window(
            query,
            vector_candidate_limit,
            self._row_target(limit, offset, query_text),
            trace=trace,
        )
        vector_results = window.ranked_rows()[:candidate_limit]
        vector_ms = (time.perf_counter() - vector_start) * 1000
        # Trigger: with reranking disabled the vector leg expands internally and can
        # hydrate more rows than the fusion window it returns.
        # Why: rows cut here never fuse — left in the trace they would surface as
        # candidates with no rejection and no fused rank, which the response labels
        # "returned". Rows with a recorded rejection keep their chunk evidence.
        # Outcome: the trace keeps rows handed to fusion (or explicitly rejected);
        # the cut shows up as served-chunk shrinkage in the candidate_window stage.
        if trace is not None and trace.vector is not None:
            kept_row_keys = {(row.type, row.id) for row in vector_results}
            kept_row_keys.update(
                rejection.key
                for rejection_group in (
                    trace.vector.threshold_rejections,
                    trace.vector.filter_rejections,
                    trace.vector.missing_search_rows,
                )
                for rejection in rejection_group
            )
            if any(match.key not in kept_row_keys for match in trace.vector.chunk_matches):
                fused_chunks: dict[SearchIndexKey, list[tuple[str, float, int | None]]] = {}
                for chunk_match in trace.vector.chunk_matches:
                    if chunk_match.key in kept_row_keys:
                        fused_chunks.setdefault(chunk_match.key, []).append(
                            (chunk_match.chunk_key, chunk_match.similarity, chunk_match.entity_id)
                        )
                trace.vector = build_vector_stage(
                    previous=trace.vector,
                    chunk_matches=fused_chunks,
                )
        fusion_start = time.perf_counter()

        with logfire.span(
            "search.fusion", fts_count=len(fts_results), vector_count=len(vector_results)
        ) as fusion_span:
            fusion = fuse(fts_results, vector_results)
            fusion_span.set_attribute("result_count", len(fusion.ranked))
        fusion_ms = (time.perf_counter() - fusion_start) * 1000
        if trace is not None:
            relaxed_fallback_used = (
                trace.fts.relaxed_fallback_used if trace.fts is not None else False
            )
            trace.fts = build_fts_page_stage(
                [((row.type, row.id), row.score or 0.0) for row in fts_results],
                normalized_scores=fusion.fts_scores,
                entity_ids={(row.type, row.id): row.entity_id for row in fts_results},
                fts_max_abs=fusion.fts_max,
                relaxed_fallback_used=relaxed_fallback_used,
                fts_ms=fts_ms,
            )
            trace.fusion = build_fusion_stage(
                formula_version=FUSION_FORMULA_VERSION,
                bonus=FUSION_BONUS,
                fts_scores=fusion.fts_scores,
                fts_ranks=fusion.fts_ranks,
                vector_scores=fusion.vector_scores,
                vector_ranks=fusion.vector_ranks,
                ranked_scores=fusion.ranked,
                fusion_ms=fusion_ms,
            )

        if rerank is None:
            # The disabled path stays cheap by materializing only the requested page.
            output = await self._materialize(
                fusion.rows(fusion.ranked[offset : offset + limit]),
                window,
                vector_keys=fusion.vector_scores.keys(),
            )
        else:
            # --- Fixed reranked prefix, read from this one retrieval (#1557) ---
            # The prefix is the fusion the fixed window would compute: its leading
            # FTS rows and its leading vector rows. Both legs of a larger window only
            # extend those rankings, so every page of a query reranks the same rows
            # with the same documents, whatever window its depth asked for.
            fixed_limit = self._rerank_candidate_limit(rerank)
            prefix_vector_rows, prefix_bound = self._prefix_rows(window, fixed_limit, query, rerank)
            prefix_fusion = fuse(fts_results[:fixed_limit], prefix_vector_rows)
            # The reranker reads the prefix's text, so it is materialized first.
            prefix = await self._materialize(
                prefix_fusion.rows(prefix_fusion.ranked[: rerank.candidates]),
                window,
                vector_keys=prefix_fusion.vector_scores.keys(),
                position_bound=prefix_bound,
            )
            prefix_keys = {row_key(row) for row in prefix}

            # Trigger: rows outside the prefix, including rows only a deeper window found.
            # Why: score fusion can strengthen a row when its second signal appears in a
            #     larger window, moving it across a page already returned.
            # Outcome: order the tail by where each row first entered either ranking —
            #     its FTS rank or its best chunk's vector position. That cannot improve
            #     as the window grows, so each larger window only appends to the tail.
            admission = dict(fusion.fts_ranks)
            for key in fusion.vector_scores:
                admission[key] = min(admission.get(key, window.position(key)), window.position(key))
            tail_entries = sorted(
                (entry for entry in fusion.ranked if entry[0] not in prefix_keys),
                key=lambda entry: (admission[entry[0]], entry[0]),
            )
            output = await self._rerank_and_paginate(
                rerank,
                query_text,
                prefix,
                fusion.rows(tail_entries),
                offset=offset,
                limit=limit,
                trace=trace,
            )
            output = await self._materialize_unread(
                output, prefix_keys, window, vector_keys=fusion.vector_scores.keys()
            )
        total_ms = (time.perf_counter() - query_start) * 1000
        if total_ms > 2500:
            logger.warning(
                "[SEMANTIC_SLOW_QUERY] Semantic query timing: scope={scope} "
                "retrieval_mode={retrieval_mode} query_length={query_length} "
                "candidate_limit={candidate_limit} fts_count={fts_count} "
                "vector_count={vector_count} fts_ms={fts_ms:.2f} vector_ms={vector_ms:.2f} "
                "fusion_ms={fusion_ms:.2f} total_ms={total_ms:.2f}",
                scope=self.scope.project_ids,
                retrieval_mode="hybrid",
                query_length=len(query_text),
                candidate_limit=candidate_limit,
                fts_count=len(fts_results),
                vector_count=len(vector_results),
                fts_ms=fts_ms,
                vector_ms=vector_ms,
                fusion_ms=fusion_ms,
                total_ms=total_ms,
            )
        return output


# --- The reader ---


class SearchReader:
    """Run one prepared query over one scope, whichever retrieval mode it asks for."""

    def __init__(
        self,
        scope: ProjectScope,
        fts: FtsBackend,
        semantic: SemanticSearch | None = None,
    ) -> None:
        self.scope = scope
        self.fts = fts
        self.semantic = semantic

    def _semantic(self) -> SemanticSearch:
        if self.semantic is None:
            raise SemanticSearchDisabledError(
                "Semantic search is not available in this search reader."
            )
        return self.semantic

    async def search(
        self,
        query: PreparedSearchQuery,
        *,
        limit: int,
        offset: int,
        allow_relaxed: bool = False,
        session: AsyncSession | None = None,
        candidate_keys: Sequence[SearchIndexKey] | None = None,
        trace: SearchTraceCollector | None = None,
    ) -> list[SearchIndexRow]:
        """Search the scope in the query's retrieval mode.

        ``candidate_keys`` restricts full-text results to those ``(type, id)`` search
        rows. ``None`` searches the whole scope; an empty sequence matches nothing.
        Vector and hybrid retrieval use that restriction to ask which of a known
        candidate set a filter admits instead of paging the filter's whole match set
        (#1431).

        ``allow_relaxed=True`` retries a zero-result strict multi-word query with
        OR-joined content terms. Only the hybrid path opts in: its FTS branch otherwise
        contributes nothing for question-form queries.
        """
        match query.retrieval_mode:
            case SearchRetrievalMode.FTS:
                return await self.fts.search(
                    self.scope,
                    query,
                    limit=limit,
                    offset=offset,
                    allow_relaxed=allow_relaxed,
                    session=session,
                    candidate_keys=candidate_keys,
                    trace=trace,
                )
            case SearchRetrievalMode.VECTOR:
                if not vector_eligible(query):
                    raise ValueError(
                        "Vector retrieval requires a non-empty text query and does not support "
                        "title/permalink-only searches."
                    )
                return await self._semantic().vector_only(
                    query, limit=limit, offset=offset, trace=trace
                )
            case SearchRetrievalMode.HYBRID:
                if not vector_eligible(query):
                    raise ValueError(
                        "Hybrid retrieval requires a non-empty text query and does not support "
                        "title/permalink-only searches."
                    )
                return await self._semantic().hybrid(query, limit=limit, offset=offset, trace=trace)
            case _:  # pragma: no cover
                assert_never(query.retrieval_mode)

    async def count(self, query: PreparedSearchQuery, *, allow_relaxed: bool = False) -> int:
        """Count full-text matches with the same filters as ``search``."""
        if query.retrieval_mode != SearchRetrievalMode.FTS:
            raise ValueError("Exact counts are only supported for full-text search retrieval.")
        return await self.fts.count(self.scope, query, allow_relaxed=allow_relaxed)
