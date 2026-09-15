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
from collections.abc import Sequence
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
from basic_memory.repository.search_index_row import SearchIndexKey, SearchIndexRow
from basic_memory.repository.search_query import PreparedSearchQuery
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.search_trace import (
    BelowThreshold,
    FilteredOut,
    HydrationDropKey,
    HydrationDropped,
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
    VectorKey,
    VectorMatch,
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


def parse_chunk_key(chunk_key: str) -> SearchIndexKey:
    """Parse a chunk key like ``observation:5:0`` into ``(type, search_index_id)``."""
    parts = chunk_key.split(":")
    return parts[0], int(parts[1])


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
    chunk_text: str
    similarity: float


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

    # --- Vector nearest neighbours through the ready manifest ---

    @logfire.instrument("search.vector_query", extract_args=False)
    async def _run_vector_query(
        self,
        session: AsyncSession,
        query_embedding: list[float],
        candidate_limit: int,
        *,
        trace: SearchTraceCollector | None = None,
    ) -> list[HydratedChunk]:
        """Query the configured adapter and hydrate only live, ready manifest rows."""
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
                query_embedding, limit=candidate_limit, projects=self.scope
            )
            if trace is not None:
                trace.readiness = await read_manifest_readiness(
                    session,
                    self.scope,
                    self.vector.index_name,
                    self.vector.embedding_model,
                )
            return await self._hydrate_vector_matches(session, matches, trace=trace)

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

        chunks_by_key: dict[VectorKey, str] = {}
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
                    "SELECT entity_id, chunk_key, chunk_text FROM search_vector_chunks "
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
                    ): str(row["chunk_text"])
                    for row in result.mappings().all()
                }
            )
        hydrated = [
            HydratedChunk(
                entity_id=match.key.entity_id,
                chunk_key=match.key.chunk_key,
                chunk_text=chunks_by_key[match.key],
                similarity=match.similarity,
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
            malformed_drops: list[HydrationDropped] = []
            for chunk in hydrated:
                try:
                    key = parse_chunk_key(chunk.chunk_key)
                except (ValueError, IndexError):
                    # A hydrated chunk with an unparseable key silently vanishes from
                    # retrieval; the trace must name it or the stage counts lie.
                    malformed_drops.append(
                        HydrationDropped(
                            entity_id=chunk.entity_id,
                            chunk_key=chunk.chunk_key,
                            similarity=chunk.similarity,
                            reason="malformed_key",
                            stored_model=None,
                            stored_index=None,
                        )
                    )
                    continue
                chunk_matches.setdefault(key, []).append(
                    (chunk.chunk_key, chunk.similarity, chunk.entity_id)
                )
            trace.vector = build_vector_stage(
                previous=trace.vector,
                adapter_match_count=len(matches),
                # Malformed keys are dropped, not served — counting them as output
                # would contradict the malformed_key rejection listed alongside.
                hydrated_count=len(hydrated) - len(malformed_drops),
                drops=(*drops, *malformed_drops),
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
                from_id, to_id, relation_type, entity_id, content_snippet,
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
            filtered_rows = await self.fts.search(
                self.scope,
                filter_query,
                # The restriction, not this limit, is what bounds the result: one row per
                # requested key, since (id, type, project_id) identifies a search row.
                limit=len(batch),
                offset=0,
                candidate_keys=batch,
            )
            allowed_keys.update((row.type, row.id) for row in filtered_rows if row.id is not None)
        return allowed_keys

    # --- Reranking ---

    async def _rerank_and_paginate(
        self,
        query_text: str,
        rows: list[SearchIndexRow],
        *,
        offset: int,
        limit: int,
        stable_rows: list[SearchIndexRow] | None = None,
        trace: SearchTraceCollector | None = None,
    ) -> list[SearchIndexRow]:
        """Rerank the top candidates, then return the requested ``[offset:offset+limit]`` page.

        Trigger: a reranker is configured and there is a real query.
        Why: bi-encoder/FTS ranking lands the gold document in the top-N but often
        just below the top-k cutoff (#950); a cross-encoder that reads query and
        document together recovers those near-misses.
        Outcome: the first ``reranker_candidates`` rows are reordered by reranker
        relevance (which replaces ``score``); the requested page is sliced from the
        reordered list.

        Every non-empty page rescores the same fixed prefix before slicing so the
        untouched tail can be demoted onto the reranker's public ``[0, 1]`` scale.
        """
        page_end = offset + limit
        rerank = self._active_rerank(query_text)
        if rerank is None:
            return rows[offset:page_end]

        # Trigger: pagination needs more rows than the fixed rerank retrieval window.
        # Why: an expanded retrieval may introduce or strengthen raw candidates, but
        # letting them replace the original prefix causes duplicates and skips.
        # Outcome: the fixed window owns prefix membership; the expanded result only
        # supplies new, de-duplicated tail rows.
        pool_source = stable_rows if stable_rows is not None else rows
        pool = pool_source[: rerank.candidates]
        pool_keys = {(row.type, row.id) for row in pool}
        tail = [row for row in rows if (row.type, row.id) not in pool_keys]
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
                stable_pool_refetched=trace.stable_pool_refetched,
                rerank_ms=(time.perf_counter() - rerank_start) * 1000,
            )
        return reranked_rows[offset:page_end]

    # --- Vector-only retrieval ---

    async def vector_only(
        self,
        query: PreparedSearchQuery,
        *,
        limit: int,
        offset: int,
        candidate_limit: int | None = None,
        apply_rerank: bool = True,
        emit_observability_log: bool = True,
        trace: SearchTraceCollector | None = None,
    ) -> list[SearchIndexRow]:
        """Run vector-only search returning chunk-level results.

        Returns individual search_index rows (entities, observations, relations)
        ranked by vector similarity. Each observation or relation is a first-class
        result, not collapsed into its parent entity.

        ``candidate_limit`` is supplied only by a composed retrieval stage that
        already sized the shared candidate pool.
        """
        # An empty scope admits no rows; embedding the query would buy nothing.
        if self.scope.is_empty:
            return []
        query_text = (query.search_text or "").strip()
        if candidate_limit is None:
            candidate_limit = self._candidate_limit(limit, offset, query_text)
        query_start = time.perf_counter()
        embed_start = time.perf_counter()
        with logfire.span("search.embed_query", query_chars=len(query_text)):
            query_embedding = await self.vector.embedding_provider.embed_query(query_text)
        embed_ms = (time.perf_counter() - embed_start) * 1000
        vector_query_start = time.perf_counter()

        # Constraint: vector adapters may open their own session, while the SQLite
        # test/runtime pool can contain only one connection. A plain AsyncSession
        # defers checkout until hydration runs after adapter search has released it.
        async with self.session_maker() as session:
            vector_rows = await self._run_vector_query(
                session,
                query_embedding,
                candidate_limit,
                trace=trace,
            )
        vector_query_ms = (time.perf_counter() - vector_query_start) * 1000
        vector_row_count = len(vector_rows)
        hydrate_ms = 0.0

        if trace is not None:
            trace.vector = build_vector_stage(
                previous=trace.vector,
                effective_min_similarity=(
                    query.min_similarity
                    if query.min_similarity is not None
                    else self.vector.min_similarity
                ),
                min_similarity_source=("query" if query.min_similarity is not None else "config"),
                embed_ms=embed_ms,
                vector_query_ms=vector_query_ms,
            )

        def _log_vector_summary() -> None:
            if not emit_observability_log:
                return

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
                    vector_row_count=vector_row_count,
                    embed_ms=embed_ms,
                    vector_query_ms=vector_query_ms,
                    hydrate_ms=hydrate_ms,
                    total_ms=total_ms,
                )

        if not vector_rows:
            _log_vector_summary()
            return []

        hydrate_start = time.perf_counter()
        # Build per-search_index_row similarity scores from chunk-level results.
        # Each chunk_key encodes the search_index row type and id; keep both as the
        # key because different row types can share the same numeric id (#982).
        # Track the best similarity per row (for ranking) and all chunks (for context).
        similarity_by_si_key: dict[SearchIndexKey, float] = {}
        chunks_by_si_key: dict[SearchIndexKey, list[tuple[float, str]]] = {}
        for chunk in vector_rows:
            try:
                si_key = parse_chunk_key(chunk.chunk_key)
            except (ValueError, IndexError):
                # A chunk without a parseable key names no search row to rank.
                continue
            current = similarity_by_si_key.get(si_key)
            if current is None or chunk.similarity > current:
                similarity_by_si_key[si_key] = chunk.similarity
            chunks_by_si_key.setdefault(si_key, []).append((chunk.similarity, chunk.chunk_text))

        if not similarity_by_si_key:
            hydrate_ms = (time.perf_counter() - hydrate_start) * 1000
            _log_vector_summary()
            return []

        # Filter out results below the minimum similarity threshold.
        # Per-query min_similarity overrides the configured default.
        effective_min_similarity = (
            query.min_similarity if query.min_similarity is not None else self.vector.min_similarity
        )
        if effective_min_similarity > 0.0:
            if trace is not None:
                threshold_rejections = tuple(
                    BelowThreshold(key=key, similarity=value, threshold=effective_min_similarity)
                    for key, value in similarity_by_si_key.items()
                    if value < effective_min_similarity
                )
                trace.vector = build_vector_stage(
                    previous=trace.vector,
                    threshold_rejections=threshold_rejections,
                )
            similarity_by_si_key = {
                k: v for k, v in similarity_by_si_key.items() if v >= effective_min_similarity
            }
            if not similarity_by_si_key:
                hydrate_ms = (time.perf_counter() - hydrate_start) * 1000
                _log_vector_summary()
                return []

        # Fetch the actual search_index rows. Colliding (type, id) keys share one
        # bare id, so deduplicate while preserving first-seen order.
        si_ids = list(dict.fromkeys(si_id for _, si_id in similarity_by_si_key))
        search_index_rows = await self._fetch_search_index_rows_by_ids(si_ids)
        if trace is not None:
            trace.vector = build_vector_stage(
                previous=trace.vector,
                missing_search_rows=tuple(
                    MissingSearchRow(key=key)
                    for key in similarity_by_si_key
                    if key not in search_index_rows
                ),
            )

        if query.has_filters:
            allowed_keys = await self._filter_candidate_keys(list(search_index_rows), query)
            if trace is not None:
                trace.vector = build_vector_stage(
                    previous=trace.vector,
                    filter_rejections=tuple(
                        FilteredOut(key=key) for key in search_index_rows if key not in allowed_keys
                    ),
                )
            search_index_rows = {k: v for k, v in search_index_rows.items() if k in allowed_keys}

        ranked_rows: list[SearchIndexRow] = []
        for si_key, similarity in similarity_by_si_key.items():
            row = search_index_rows.get(si_key)
            if row is None:
                continue

            # Small notes: return full content so the answer is always present.
            # Large notes: return top-N most relevant chunks for richer context.
            content_snippet = row.content_snippet or ""
            if content_snippet and len(content_snippet) <= SMALL_NOTE_CONTENT_LIMIT:
                matched_chunk_text = content_snippet
            else:
                si_chunks = chunks_by_si_key.get(si_key, [])
                si_chunks.sort(key=lambda c: c[0], reverse=True)
                top_texts = [chunk_text for _, chunk_text in si_chunks[:TOP_CHUNKS_PER_RESULT]]
                matched_chunk_text = "\n---\n".join(top_texts) if top_texts else None

            ranked_rows.append(
                replace(
                    row,
                    score=similarity,
                    matched_chunk_text=matched_chunk_text,
                )
            )

        ranked_rows.sort(key=lambda item: item.score or 0.0, reverse=True)
        hydrate_ms = (time.perf_counter() - hydrate_start) * 1000
        # Rerank over the wide candidate pool, then slice to the page. Suppressed when
        # hybrid calls this internally (apply_rerank=False): hybrid reranks its own
        # fused result, and _rerank_and_paginate is a plain slice without a reranker.
        if apply_rerank:
            stable_rows = ranked_rows
            rerank = self._active_rerank(query_text)
            if rerank is not None:
                stable_candidate_limit = self._rerank_candidate_limit(rerank)
                if candidate_limit > stable_candidate_limit:
                    if trace is not None:
                        trace.stable_pool_refetched = True
                    stable_rows = await self.vector_only(
                        query,
                        limit=stable_candidate_limit,
                        offset=0,
                        candidate_limit=stable_candidate_limit,
                        apply_rerank=False,
                        emit_observability_log=False,
                        trace=None,
                    )
            output = await self._rerank_and_paginate(
                query_text,
                ranked_rows,
                offset=offset,
                limit=limit,
                stable_rows=stable_rows,
                trace=trace,
            )
        else:
            output = ranked_rows[offset : offset + limit]
        # Vector latency owns the optional rerank stage too. Logging before the
        # awaited provider call hides the feature's dominant cost and can suppress
        # the slow-query warning entirely.
        _log_vector_summary()
        return output

    # --- Hybrid score-based fusion ---

    async def hybrid(
        self,
        query: PreparedSearchQuery,
        *,
        limit: int,
        offset: int,
        candidate_limit: int | None = None,
        apply_rerank: bool = True,
        emit_observability_log: bool = True,
        trace: SearchTraceCollector | None = None,
    ) -> list[SearchIndexRow]:
        """Fuse FTS and vector results using score-based fusion.

        Uses the search_index (type, id) pair as the fusion key. The formula
        ``max(vec, fts) + FUSION_BONUS * min(vec, fts)`` preserves
        the dominant signal and rewards dual-source agreement.
        """
        if self.scope.is_empty:
            return []
        query_text = (query.search_text or "").strip()
        rerank = self._active_rerank(query_text)
        query_start = time.perf_counter()
        if candidate_limit is None:
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
            )
            fts_span.set_attribute("result_count", len(fts_results))
        fts_ms = (time.perf_counter() - fts_start) * 1000
        vector_start = time.perf_counter()
        vector_results = await self.vector_only(
            query,
            limit=candidate_limit,
            offset=0,
            # Trigger: reranking owns a bounded candidate window shared by both legs.
            # Why: the disabled path historically expands the vector leg again to
            # preserve recall when many vector chunks collapse into a few search rows.
            # Outcome: avoid double expansion only when reranking is actually active.
            candidate_limit=candidate_limit if rerank is not None else None,
            apply_rerank=False,
            emit_observability_log=False,
            trace=trace,
        )
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
            # --- Score-based fusion keyed on (type, id) ---
            # A bare row id collides across row types (independent id sequences), so
            # fusion must key on (type, id) or distinct rows would merge (#982).
            # FTS scores are normalized to [0, 1] (BM25 is unbounded).
            # Vector scores are used raw: the adapters already calibrate them to [0, 1].
            rows_by_key: dict[SearchIndexKey, SearchIndexRow] = {}

            # Normalize FTS scores to [0, 1] — handles both SQLite (negative bm25)
            # and Postgres (positive ts_rank) by using absolute values
            fts_abs = [abs(row.score or 0.0) for row in fts_results]
            fts_max = max(fts_abs) if fts_abs else 1.0

            fts_scores: dict[SearchIndexKey, float] = {}
            fts_ranks: dict[SearchIndexKey, int] = {}
            for rank, row in enumerate(fts_results):
                if row.id is None:
                    continue
                row_key = (row.type, row.id)
                norm = abs(row.score or 0.0) / fts_max if fts_max > 0 else 0.0
                # Gate: FTS scores below threshold contribute zero
                if norm < FTS_GATE_THRESHOLD:
                    norm = 0.0
                fts_scores[row_key] = norm
                fts_ranks.setdefault(row_key, rank)
                rows_by_key[row_key] = row

            if trace is not None:
                relaxed_fallback_used = (
                    trace.fts.relaxed_fallback_used if trace.fts is not None else False
                )
                trace.fts = build_fts_page_stage(
                    [((row.type, row.id), row.score or 0.0) for row in fts_results],
                    normalized_scores=fts_scores,
                    entity_ids={(row.type, row.id): row.entity_id for row in fts_results},
                    fts_max_abs=fts_max,
                    relaxed_fallback_used=relaxed_fallback_used,
                    fts_ms=fts_ms,
                )

            vec_scores: dict[SearchIndexKey, float] = {}
            vec_ranks: dict[SearchIndexKey, int] = {}
            for rank, row in enumerate(vector_results):
                if row.id is None:
                    continue
                row_key = (row.type, row.id)
                # Trigger: no re-normalization by vec_max
                # Why: vector similarity is already calibrated [0, 1]; re-normalizing
                # inflates weak matches when the entire result set is mediocre
                vec_scores[row_key] = row.score or 0.0
                vec_ranks.setdefault(row_key, rank)
                rows_by_key[row_key] = row

            # Fuse: max(v, f) + FUSION_BONUS * min(v, f)
            # Preserves the dominant signal; bonus rewards dual-source agreement.
            # Output range: [0, 1.3] for dual-source, [0, 1.0] for single-source.
            fused_scores: dict[SearchIndexKey, float] = {}
            for row_key in fts_scores.keys() | vec_scores.keys():
                v = vec_scores.get(row_key, 0.0)
                f = fts_scores.get(row_key, 0.0)
                fused_scores[row_key] = max(v, f) + FUSION_BONUS * min(v, f)

            ranked = sorted(fused_scores.items(), key=lambda item: item[1], reverse=True)
            fusion_span.set_attribute("result_count", len(ranked))
        fusion_ms = (time.perf_counter() - fusion_start) * 1000
        if trace is not None:
            trace.fusion = build_fusion_stage(
                formula_version=FUSION_FORMULA_VERSION,
                bonus=FUSION_BONUS,
                fts_scores=fts_scores,
                fts_ranks=fts_ranks,
                vector_scores=vec_scores,
                vector_ranks=vec_ranks,
                ranked_scores=ranked,
                fusion_ms=fusion_ms,
            )

        def _materialize(entry: tuple[SearchIndexKey, float]) -> SearchIndexRow:
            row_key, fused_score = entry
            row = rows_by_key[row_key]
            # FTS-only hits use the bounded content preview and its truncation metadata.
            # Copying the full note into matched_chunk bypasses that response bound.
            return replace(row, score=fused_score)

        # Rerank the top fused candidates before paginating. When reranking is active
        # we materialize the whole candidate list (cheap next to a cross-encoder call)
        # and hand it to the shared paginate helper; the disabled path stays cheap by
        # materializing only the requested page.
        if apply_rerank and rerank is not None:
            candidates = [_materialize(entry) for entry in ranked]
            stable_candidates = candidates
            stable_candidate_limit = self._rerank_candidate_limit(rerank)
            if candidate_limit > stable_candidate_limit:
                if trace is not None:
                    trace.stable_pool_refetched = True
                stable_candidates = await self.hybrid(
                    query,
                    limit=stable_candidate_limit,
                    offset=0,
                    candidate_limit=stable_candidate_limit,
                    apply_rerank=False,
                    emit_observability_log=False,
                    trace=None,
                )
                stable_keys = {(row.type, row.id) for row in stable_candidates}
                expanded_tail = [entry for entry in ranked if entry[0] not in stable_keys]

                # Trigger: deeper pages expand the FTS/vector retrieval windows.
                # Why: score fusion can strengthen an existing row when its second
                # signal appears later, moving it across a page already returned.
                # Outcome: freeze the fixed fused universe, then order newly admitted
                # rows by their earliest source rank. That rank cannot improve after a
                # row first appears, so each larger window only appends to the tail.
                expanded_tail.sort(
                    key=lambda entry: (
                        min(
                            fts_ranks.get(entry[0], candidate_limit),
                            vec_ranks.get(entry[0], candidate_limit),
                        ),
                        entry[0],
                    )
                )
                candidates = stable_candidates + [_materialize(entry) for entry in expanded_tail]
            output = await self._rerank_and_paginate(
                query_text,
                candidates,
                offset=offset,
                limit=limit,
                stable_rows=stable_candidates,
                trace=trace,
            )
        else:
            output = [_materialize(entry) for entry in ranked[offset : offset + limit]]
        total_ms = (time.perf_counter() - query_start) * 1000
        if emit_observability_log and total_ms > 2500:
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
                "Semantic search is disabled. Set BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true."
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
