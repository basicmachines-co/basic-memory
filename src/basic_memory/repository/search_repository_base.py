"""Abstract base class for search repository implementations."""

import hashlib
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional, cast

import logfire as logfire
from loguru import logger
from sqlalchemy import Executable, Result, inspect, text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.repository import semantic_vector_sync
from basic_memory.repository.embedding_provider import (
    EmbeddingProvider,
    embedding_provider_identity,
)
from basic_memory.repository.embedding_provider_factory import (
    configured_embedding_provider_identity,
)
from basic_memory.repository.rerank_provider import (
    RerankProvider,
    build_rerank_document,
    demote_tail_scores,
    validate_rerank_scores,
)
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.script_ngrams import build_script_ngrams
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
from basic_memory.repository.semantic_chunking import (
    SemanticSourceRow,
    VectorChunkRecord,
    build_entity_fingerprint,
    build_vector_chunk_records,
    split_text_into_chunks,
)
from basic_memory.repository.semantic_errors import (
    SemanticDependenciesMissingError,
    SemanticSearchDisabledError,
    SemanticVectorIndexExtensionError,
)
from basic_memory.repository.semantic_vector_index import (
    SemanticVectorIndex,
    SemanticVectorIndexReconciler,
    VectorDeletion,
    VectorKey,
    VectorMatch,
    VectorRecord,
)
from basic_memory.repository.semantic_vector_sync import (
    EmbeddingPersistenceResult as _EmbeddingPersistenceResult,
    EntitySyncRuntime as _EntitySyncRuntime,
    EntityVectorShardPlan as _EntityVectorShardPlan,
    PendingEmbeddingJob as _PendingEmbeddingJob,
    PreparedEntityVectorSync as _PreparedEntityVectorSync,
    StagedVectorDeletion as _StagedVectorDeletion,
    VectorChunkState,
)
from basic_memory.runtime.storage import RUNTIME_MARKDOWN_CONTENT_TYPE
from basic_memory.runtime.vector_sync import VectorSyncBatchResult
from basic_memory.schemas.search import (
    SearchItemType,
    SearchRetrievalMode,
    normalize_file_path_prefix,
)
from basic_memory.temporal import TemporalFilter
from basic_memory.utils import ensure_timezone_aware

# --- Semantic search constants ---

VECTOR_FILTER_SCAN_LIMIT = 50000
VECTOR_HYDRATION_BATCH_SIZE = 250

# The manifest conditions under which semantic retrieval will use a stored vector.
# Vector hydration (_hydrate_vector_matches) admits exactly these rows, so anything
# failing them is invisible to search: a chunk left behind by an embedding-model or
# vector-index change, or one still pending. Readiness reporting must apply the same
# predicate — calling such a row "embedded" would report an index settled that
# retrieval cannot answer from, which is the class of lie #1414 exists to remove.
# Both callers bind :project_id, :vector_index, and :embedding_model.
CURRENT_VECTOR_MANIFEST_PREDICATE = (
    "project_id = :project_id "
    "AND vector_index = :vector_index "
    "AND embedding_model = :embedding_model "
    "AND embedding_status = 'ready'"
)
# Over-fetch factor for the rerank candidate chunk pool: chunks collapse to unique
# (type, id) rows before reranking, so fetch several times reranker_candidates chunks
# to keep enough unique documents in the rerank window.
RERANK_POOL_CHUNK_FANOUT = 4
FUSION_BONUS = 0.3
FUSION_FORMULA_VERSION = "max+0.3*min/v1"
FTS_GATE_THRESHOLD = 0.0
TOP_CHUNKS_PER_RESULT = 5
SMALL_NOTE_CONTENT_LIMIT = 2000
OVERSIZED_ENTITY_VECTOR_SHARD_SIZE = semantic_vector_sync.OVERSIZED_ENTITY_VECTOR_SHARD_SIZE
_SQLITE_MAX_PREPARE_WINDOW = semantic_vector_sync.SQLITE_MAX_PREPARE_WINDOW
_BUILT_IN_VECTOR_INDEX_NAMES = frozenset({"pgvector", "sqlite-vec"})

# Entity, observation, and relation rows in search_index carry ids from independent
# auto-increment sequences, so a bare id is ambiguous across row types. Every map in
# the vector/hybrid retrieval path must key rows by (type, id) to avoid collisions.
type SearchIndexKey = tuple[str, int]
type StoredEmbeddingStatus = Literal["pending", "ready"]


@dataclass(frozen=True, slots=True)
class ChunkManifestRow:
    """One persisted vector-chunk manifest row."""

    entity_id: int
    chunk_key: str
    chunk_text: str
    source_hash: str
    entity_fingerprint: str
    embedding_model: str
    vector_index: str
    embedding_status: StoredEmbeddingStatus
    updated_at: datetime

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "ChunkManifestRow":
        """Hydrate one portable manifest row from SQLite or PostgreSQL."""
        raw_status = str(row["embedding_status"])
        match raw_status:
            case "pending":
                embedding_status: StoredEmbeddingStatus = "pending"
            case "ready":
                embedding_status = "ready"
            case _:
                raise ValueError(f"Unknown vector chunk embedding status: {raw_status!r}")

        return cls(
            entity_id=int(row["entity_id"]),
            chunk_key=str(row["chunk_key"]),
            chunk_text=str(row["chunk_text"]),
            source_hash=str(row["source_hash"]),
            entity_fingerprint=str(row["entity_fingerprint"]),
            embedding_model=str(row["embedding_model"]),
            vector_index=str(row["vector_index"]),
            embedding_status=embedding_status,
            updated_at=row["updated_at"],
        )

    def __post_init__(self) -> None:
        """Restore a timezone-aware datetime from either backend's raw value."""
        updated_at = self.updated_at
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)
        object.__setattr__(self, "updated_at", ensure_timezone_aware(updated_at))


def file_path_prefix_condition(
    file_path_prefix: Optional[str],
    params: Dict[str, Any],
) -> Optional[str]:
    """Build the SQL scoping search rows to one directory subtree of the project.

    One implementation, shared verbatim by both backends: a subtree scope that
    means different things on SQLite and Postgres would report an exact total
    for a match set the other dialect never produces.

    Boundary: the compared prefix carries its trailing separator, so "specs"
    admits "specs/api.md" and never "specs-archive/api.md".

    Why an explicit-length comparison rather than ``file_path LIKE 'specs/%'``:
    LIKE reads "_" and "%" as wildcards and both are ordinary characters in a
    directory name, so "my_notes" would silently also admit "my-notes"; and
    LIKE case-folds differently per backend — SQLite's is ASCII-case-insensitive
    while Postgres's is case-sensitive — so one filter would answer two
    different questions. SUBSTR equality has no pattern language to escape and
    compares under each backend's deterministic default text collation, which is
    byte equality on both, so the dialects match exactly the same rows.
    """
    normalized = normalize_file_path_prefix(file_path_prefix)
    if normalized is None:
        return None
    prefix = f"{normalized}/"
    params["file_path_prefix"] = prefix
    params["file_path_prefix_length"] = len(prefix)
    return "SUBSTR(search_index.file_path, 1, :file_path_prefix_length) = :file_path_prefix"


def metadata_filter_content_type_condition(params: Dict[str, Any]) -> str:
    """Build the SQL restricting a metadata-filtered query to Markdown notes.

    Frontmatter is a Markdown-only construct, but every indexed file — PDF,
    image, binary — gets its own ENTITY row whose ``entity_metadata`` carries no
    keys at all. A positive predicate can never match one, so this constraint
    was invisible until ``{"key": None}`` arrived: ``IS NULL`` is satisfied by
    the *absence* of a key, which is exactly the state every regular file is in,
    and the whole non-note half of a project counted into an exact total.

    Applied to any metadata filter, not just the null one, so the
    frontmatter-only contract is a property of the clause rather than of which
    operator happened to be used. Shared by both backends for the same reason
    the subtree scope is: a filter that admits different rows per dialect would
    report an exact total for a match set the other never produces.
    """
    params["metadata_filter_content_type"] = RUNTIME_MARKDOWN_CONTENT_TYPE
    return "entity.content_type = :metadata_filter_content_type"


# SQLite's LIKE has no default escape character, and Postgres's is already the
# backslash, so naming this one explicitly in every pattern is what lets a single
# escaped pattern mean the same thing on both backends.
_LIKE_ESCAPE_CHARACTER = "\\"


def metadata_contains_like_condition(
    extract_expr: str,
    value: Any,
    *,
    param_prefix: str,
    params: Dict[str, Any],
) -> str:
    """Build the compatibility half of an array-contains metadata filter.

    The primary half of a ``{"tags": ["security"]}`` filter asks JSON whether the
    array holds the element — ``json_each`` on SQLite, ``@>`` on Postgres — and
    answers only when the stored value really is a JSON array. Frontmatter
    written before tags were normalized can hold the array's *text* instead,
    either JSON-quoted ('["security", "auth"]') or as a Python repr
    ("['security', 'auth']"), and only a substring match finds an element inside
    those. Hence a pattern per quote style, and hence the pattern-language
    problem this function exists to solve.

    LIKE reads "%" and "_" in the searched-for value as wildcards, so
    interpolating the value raw turned `tags has 100%` into a pattern that also
    matched "100-percent" — a wrong hit and an inflated exact total, produced by
    the branch the caller only meant as a fallback. Escaping both wildcards and
    the escape character itself makes the value literal again.

    Shared by both backends for the same reason the subtree scope is: a filter
    that admits different rows per dialect would report an exact total for a
    match set the other never produces.
    """
    escaped = (
        str(value)
        .replace(_LIKE_ESCAPE_CHARACTER, _LIKE_ESCAPE_CHARACTER * 2)
        .replace("%", f"{_LIKE_ESCAPE_CHARACTER}%")
        .replace("_", f"{_LIKE_ESCAPE_CHARACTER}_")
    )
    double_quoted_param = f"{param_prefix}_like"
    single_quoted_param = f"{param_prefix}_like_single"
    params[double_quoted_param] = f'%"{escaped}"%'
    params[single_quoted_param] = f"%'{escaped}'%"
    escape_clause = f" ESCAPE '{_LIKE_ESCAPE_CHARACTER}'"
    return (
        f"{extract_expr} LIKE :{double_quoted_param}{escape_clause} "
        f"OR {extract_expr} LIKE :{single_quoted_param}{escape_clause}"
    )


async def purge_stale_search_index_rows(
    session_maker: async_sessionmaker[AsyncSession],
    project_id: int,
) -> int:
    """Reconciliation sweep: delete search_index rows whose backing row is gone.

    search_index is derived, eventually consistent state; this sweep converges
    DB-only divergence (#1214/#1226) that file-checksum change detection cannot
    see. Plain project-scoped DELETEs - no locks, no file re-parse. The SQL text
    is identical on SQLite FTS5 and Postgres (precedent: migration 2d26b287813b).
    """
    statements = (
        "DELETE FROM search_index WHERE project_id = :project_id "
        "AND entity_id NOT IN (SELECT id FROM entity WHERE project_id = :project_id)",
        "DELETE FROM search_index WHERE project_id = :project_id "
        "AND type = 'observation' "
        "AND id NOT IN (SELECT id FROM observation WHERE project_id = :project_id)",
        "DELETE FROM search_index WHERE project_id = :project_id "
        "AND type = 'relation' "
        "AND id NOT IN (SELECT id FROM relation WHERE project_id = :project_id)",
    )
    purged = 0
    async with db.scoped_session(session_maker) as session:
        for statement in statements:
            result = cast(
                CursorResult[Any],
                await session.execute(text(statement), {"project_id": project_id}),
            )
            purged += max(result.rowcount or 0, 0)
    logger.info("Purged stale search index rows", project_id=project_id, purged=purged)
    return purged


class SearchRepositoryBase(ABC):
    """Abstract base class for backend-specific search repository implementations.

    This class defines the common interface that all search repositories must implement,
    regardless of whether they use SQLite FTS5 or Postgres tsvector for full-text search.

    Shared semantic search logic (chunking, embedding orchestration, hybrid score-based fusion)
    lives here. Backend-specific operations are delegated to abstract hooks.

    Concrete implementations:
    - SQLiteSearchRepository: Uses FTS5 virtual tables with MATCH queries
    - PostgresSearchRepository: Uses tsvector/tsquery with GIN indexes
    """

    # --- Subclass-populated attributes ---
    _semantic_enabled: bool
    _app_config: BasicMemoryConfig
    _semantic_vector_k: int
    _semantic_min_similarity: float
    _embedding_provider: Optional[EmbeddingProvider]
    # Class-level defaults: a repo with no reranker configured (or a lightweight
    # test double that bypasses __init__) safely skips reranking.
    _rerank_provider: Optional[RerankProvider] = None
    _reranker_candidates: int = 20
    _reranker_max_document_chars: int = 0
    _semantic_embedding_sync_batch_size: int
    _vector_dimensions: int
    _vector_tables_initialized: bool
    _semantic_vector_index: SemanticVectorIndex
    _semantic_vector_index_name: str = ""

    def __init__(self, session_maker: async_sessionmaker[AsyncSession], project_id: int):
        """Initialize with session maker and project_id filter.

        Args:
            session_maker: SQLAlchemy session maker
            project_id: Project ID to filter all operations by

        Raises:
            ValueError: If project_id is None or invalid
        """
        if project_id is None or project_id <= 0:  # pragma: no cover
            raise ValueError("A valid project_id is required for SearchRepository")

        self.session_maker = session_maker
        self.project_id = project_id

    async def semantic_effectively_enabled(self) -> bool:
        """Return whether semantic retrieval can actually run for this repository.

        Configuration is the default signal. Backends with a startup runtime
        fallback (SQLite degrades to keyword-only search when sqlite-vec cannot
        load, #711) override this with a runtime probe so per-request instances
        honor the degraded state instead of trusting still-enabled config.
        """
        return self._semantic_enabled

    @property
    def configured_embedding_model(self) -> str:
        """Return the configured persisted embedding identity without loading a model."""
        return configured_embedding_provider_identity(self._app_config)

    @property
    def configured_vector_index(self) -> str:
        """Return the configured vector-index identity."""
        return self._semantic_vector_index_name

    @property
    def configured_min_similarity(self) -> float:
        """Return the configured vector similarity floor."""
        return self._semantic_min_similarity

    @property
    def configured_reranker_model(self) -> str | None:
        """Return the configured reranker identity without invoking it."""
        return self._rerank_provider.model_name if self._rerank_provider is not None else None

    @property
    def configured_reranker_candidates(self) -> int:
        """Return the fixed maximum reranker pool size."""
        return self._reranker_candidates

    # ------------------------------------------------------------------
    # Abstract methods — FTS and schema (backend-specific)
    # ------------------------------------------------------------------

    @abstractmethod
    async def init_search_index(self) -> None:
        """Create or recreate the search index.

        Backend-specific implementations:
        - SQLite: CREATE VIRTUAL TABLE using FTS5
        - Postgres: CREATE TABLE with tsvector column and GIN indexes
        """
        pass

    @abstractmethod
    def _prepare_search_term(self, term: str, is_prefix: bool = True) -> str:
        """Prepare a search term for backend-specific query syntax.

        Args:
            term: The search term to prepare
            is_prefix: Whether to add prefix search capability

        Returns:
            Formatted search term for the backend

        Backend-specific implementations:
        - SQLite: Quotes FTS5 special characters, adds * wildcards
        - Postgres: Converts to tsquery syntax with :* prefix operator
        """
        pass

    @abstractmethod
    async def search(
        self,
        search_text: Optional[str] = None,
        permalink: Optional[str] = None,
        permalink_match: Optional[str] = None,
        title: Optional[str] = None,
        note_types: Optional[List[str]] = None,
        after_date: Optional[datetime] = None,
        search_item_types: Optional[List[SearchItemType]] = None,
        categories: Optional[List[str]] = None,
        metadata_filters: Optional[Dict[str, Any]] = None,
        file_path_prefix: Optional[str] = None,
        temporal: Optional[TemporalFilter] = None,
        retrieval_mode: SearchRetrievalMode = SearchRetrievalMode.FTS,
        min_similarity: Optional[float] = None,
        limit: int = 10,
        offset: int = 0,
        allow_relaxed: bool = False,
        *,
        trace: SearchTraceCollector | None = None,
    ) -> List[SearchIndexRow]:
        """Search across all indexed content.

        Args:
            search_text: Full-text search across title and content
            permalink: Exact permalink match
            permalink_match: Permalink pattern match (supports *)
            title: Title search
            note_types: Filter by note types (from metadata.note_type)
            after_date: Filter by created_at > after_date
            search_item_types: Filter by SearchItemType (ENTITY, OBSERVATION, RELATION)
            categories: Filter observations by exact category (e.g. "requirement")
            metadata_filters: Structured frontmatter metadata filters
            file_path_prefix: Directory subtree scope, matched against file_path
            temporal: Authored valid-time filter. Unlike after_date, which reads the
                note's edit bookkeeping, this reads the time an observation claims to
                be true of the world. Sources without such a claim are excluded.
            limit: Maximum results to return
            offset: Number of results to skip

        Returns:
            List of SearchIndexRow results with relevance scores

        Backend-specific implementations:
        - SQLite: Uses MATCH operator and bm25() for scoring
        - Postgres: Uses @@ operator and ts_rank() for scoring
        """
        pass

    async def count(
        self,
        search_text: Optional[str] = None,
        permalink: Optional[str] = None,
        permalink_match: Optional[str] = None,
        title: Optional[str] = None,
        note_types: Optional[List[str]] = None,
        after_date: Optional[datetime] = None,
        search_item_types: Optional[List[SearchItemType]] = None,
        categories: Optional[List[str]] = None,
        metadata_filters: Optional[Dict[str, Any]] = None,
        file_path_prefix: Optional[str] = None,
        temporal: Optional[TemporalFilter] = None,
        retrieval_mode: SearchRetrievalMode = SearchRetrievalMode.FTS,
        min_similarity: Optional[float] = None,
        allow_relaxed: bool = False,
    ) -> int:
        """Count results when a backend-specific COUNT query is available."""
        if retrieval_mode != SearchRetrievalMode.FTS:
            raise ValueError("Exact counts are only supported for full-text search retrieval.")
        raise NotImplementedError("Backend search repositories must implement full-text counts.")

    # ------------------------------------------------------------------
    # Abstract methods — semantic search (backend-specific DB operations)
    # ------------------------------------------------------------------

    @abstractmethod
    async def _ensure_vector_tables(self) -> None:
        """Create backend-specific vector chunk and embedding tables."""
        pass

    async def _run_vector_query(
        self,
        session: AsyncSession,
        query_embedding: list[float],
        candidate_limit: int,
        *,
        trace: SearchTraceCollector | None = None,
    ) -> list[dict[str, Any]]:
        """Query the configured adapter and hydrate only live, ready manifest rows."""
        if trace is not None:
            trace.vector = build_vector_stage(
                candidate_limit=candidate_limit,
                adapter_match_count=0,
                hydrated_count=0,
            )
        if candidate_limit <= 0:
            return []

        external_vector_index = self._semantic_vector_index_name not in _BUILT_IN_VECTOR_INDEX_NAMES
        if not external_vector_index:
            matches = await self._semantic_vector_index.search(
                query_embedding,
                limit=candidate_limit,
            )
            if trace is not None:
                trace.readiness = await read_manifest_readiness(
                    session,
                    self.project_id,
                    self._semantic_vector_index_name,
                    self._embedding_model_key(),
                )
            return await self._hydrate_vector_matches(session, matches, trace=trace)

        scan_limit = min(candidate_limit, VECTOR_FILTER_SCAN_LIMIT)
        while True:
            matches = await self._semantic_vector_index.search(
                query_embedding,
                limit=scan_limit,
            )
            if trace is not None and trace.readiness is None:
                trace.readiness = await read_manifest_readiness(
                    session,
                    self.project_id,
                    self._semantic_vector_index_name,
                    self._embedding_model_key(),
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
                    returned_chunk_keys = {
                        (int(row["entity_id"]), str(row["chunk_key"])) for row in returned
                    }
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

    async def record_entity_vector_deferrals(
        self,
        *,
        deferred_entity_ids: set[int],
        completed_entity_ids: set[int],
    ) -> None:
        """Persist which entities a sharded vector sync left unfinished.

        The single writer of `entity.vector_sync_deferred_at`, called from the one
        place that decides deferral. Readiness reads the column instead of trying
        to infer completeness from the manifest, which cannot show it: the chunks
        a shard did not schedule have no row, so an entity looks fully embedded
        after its first shard (#1440 review).
        """
        if not deferred_entity_ids and not completed_entity_ids:
            return

        # Chunked at the same bound vector hydration uses. `reindex_vectors`
        # hands every entity in the project to one batch, so an unchunked IN list
        # is one bind per entity and blows the driver's parameter cap on a large
        # project -- asyncpg stops at 32767. That would raise *after* the
        # embedding work succeeded, failing the `bm reindex` that #1414 tells
        # users to run, on exactly the projects indexing matters most for.
        #
        # Every chunk shares one transaction, so a failure part-way through rolls
        # back all of them rather than leaving some entities marked and others
        # not. Losing the whole update is safe: the markers are derived state, so
        # the next sync pass recomputes and rewrites them. A half-applied update
        # would not be -- readiness would report a mix of stale and current
        # markers with nothing to reconcile them.
        async with db.scoped_session(self.session_maker) as session:
            for entity_ids, deferred_at in (
                (sorted(deferred_entity_ids), datetime.now(timezone.utc)),
                (sorted(completed_entity_ids), None),
            ):
                for start in range(0, len(entity_ids), VECTOR_HYDRATION_BATCH_SIZE):
                    chunk = entity_ids[start : start + VECTOR_HYDRATION_BATCH_SIZE]
                    placeholders = ", ".join(f":entity_id_{index}" for index in range(len(chunk)))
                    params: dict[str, object] = {
                        "project_id": self.project_id,
                        "deferred_at": deferred_at,
                    }
                    params.update(
                        {f"entity_id_{index}": entity_id for index, entity_id in enumerate(chunk)}
                    )
                    await session.execute(
                        text(
                            "UPDATE entity SET vector_sync_deferred_at = :deferred_at "
                            f"WHERE project_id = :project_id AND id IN ({placeholders})"
                        ),
                        params,
                    )
            await session.commit()

    async def _hydrate_vector_matches(
        self,
        session: AsyncSession,
        matches: list[VectorMatch],
        *,
        trace: SearchTraceCollector | None = None,
    ) -> list[dict[str, Any]]:
        """Resolve adapter matches through the authoritative ready manifest."""
        if not matches:
            return []

        chunks_by_key: dict[VectorKey, str] = {}
        for batch_start in range(0, len(matches), VECTOR_HYDRATION_BATCH_SIZE):
            batch = matches[batch_start : batch_start + VECTOR_HYDRATION_BATCH_SIZE]
            params: dict[str, object] = {
                "project_id": self.project_id,
                "vector_index": self._semantic_vector_index_name,
                "embedding_model": self._embedding_model_key(),
            }
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
                    "WHERE " + CURRENT_VECTOR_MANIFEST_PREDICATE + " "
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
            {
                "entity_id": match.key.entity_id,
                "chunk_key": match.key.chunk_key,
                "chunk_text": chunks_by_key[match.key],
                "best_similarity": match.similarity,
            }
            for match in matches
            if match.key in chunks_by_key
        ]
        if trace is not None:
            dropped_keys = [
                HydrationDropKey(
                    entity_id=match.key.entity_id,
                    chunk_key=match.key.chunk_key,
                    similarity=match.similarity,
                    configured_index=self._semantic_vector_index_name,
                    configured_model=self._embedding_model_key(),
                )
                for match in matches
                if match.key not in chunks_by_key
            ]
            drops = await classify_hydration_drops(session, self.project_id, dropped_keys)
            chunk_matches: dict[SearchIndexKey, list[tuple[str, float, int | None]]] = {}
            malformed_drops: list[HydrationDropped] = []
            for row in hydrated:
                try:
                    key = self._parse_chunk_key(str(row["chunk_key"]))
                except (ValueError, IndexError):
                    # A hydrated chunk with an unparseable key silently vanishes from
                    # retrieval; the trace must name it or the stage counts lie.
                    malformed_drops.append(
                        HydrationDropped(
                            entity_id=int(row["entity_id"]),
                            chunk_key=str(row["chunk_key"]),
                            similarity=float(row["best_similarity"]),
                            reason="malformed_key",
                            stored_model=None,
                            stored_index=None,
                        )
                    )
                    continue
                chunk_matches.setdefault(key, []).append(
                    (str(row["chunk_key"]), float(row["best_similarity"]), int(row["entity_id"]))
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

    async def _write_embeddings(
        self,
        session: AsyncSession,
        jobs: list[tuple[int, str]],
        embeddings: list[list[float]],
    ) -> None:
        """Legacy storage hook retained for focused pre-adapter test repositories."""
        raise NotImplementedError

    async def _persist_embeddings(
        self,
        jobs: Sequence[_PendingEmbeddingJob],
        embeddings: list[list[float]],
    ) -> _EmbeddingPersistenceResult:
        """Write vectors through the adapter, then make their manifest rows ready."""
        if not jobs:
            return _EmbeddingPersistenceResult()
        if len(jobs) != len(embeddings):
            raise RuntimeError("Embedding provider returned an unexpected number of vectors.")

        for job in jobs:
            expected_source_hash = hashlib.sha256(job.chunk_text.encode("utf-8")).hexdigest()
            if job.source_hash != expected_source_hash:
                raise RuntimeError(
                    f"Embedding job source hash does not match its chunk text: {job.chunk_row_id}"
                )

        job_pairs = [(job.chunk_row_id, job.chunk_text) for job in jobs]

        # Compatibility: focused orchestration tests and third-party subclasses
        # from before the adapter contract may still override the private writer.
        # Real repositories always configure `_semantic_vector_index` and take the
        # manifest-safe path below.
        if not hasattr(self, "_semantic_vector_index"):
            async with db.scoped_session(self.session_maker) as session:
                await self._prepare_vector_session(session)
                await self._write_embeddings(session, job_pairs, embeddings)
                await session.commit()
            return _EmbeddingPersistenceResult(
                persisted_row_ids=frozenset(row_id for row_id, _chunk_text in job_pairs)
            )

        row_ids = [row_id for row_id, _chunk_text in job_pairs]
        lookup_params = {f"row_id_{index}": row_id for index, row_id in enumerate(row_ids)}
        lookup_placeholders = ", ".join(f":row_id_{index}" for index in range(len(row_ids)))
        async with db.scoped_session(self.session_maker) as session:
            connection = await session.connection()
            dialect_name = connection.dialect.name
            external_vector_index = (
                self._semantic_vector_index_name not in _BUILT_IN_VECTOR_INDEX_NAMES
            )
            lock_external_write = external_vector_index and dialect_name in {"postgresql", "sqlite"}
            if external_vector_index:
                await self._lock_external_vector_write(session)
            if external_vector_index and dialect_name == "sqlite":
                # SQLite has no SELECT FOR UPDATE. A conditional no-op write takes
                # the database write lock before extension I/O, serializing a newer
                # manifest generation behind this adapter write and ready commit.
                lookup_statement = (
                    "UPDATE search_vector_chunks SET source_hash = source_hash "
                    f"WHERE project_id = :project_id AND id IN ({lookup_placeholders}) "
                    "RETURNING id, entity_id, chunk_key, source_hash"
                )
            else:
                lock_clause = (
                    " FOR UPDATE" if external_vector_index and dialect_name == "postgresql" else ""
                )
                lookup_statement = (
                    "SELECT id, entity_id, chunk_key, source_hash FROM search_vector_chunks "
                    f"WHERE project_id = :project_id AND id IN ({lookup_placeholders})"
                    f"{lock_clause}"
                )
            result = await session.execute(
                text(lookup_statement),
                {**lookup_params, "project_id": self.project_id},
            )
            rows_by_id = {int(row["id"]): row for row in result.mappings().all()}

            missing_jobs = [job for job in jobs if job.chunk_row_id not in rows_by_id]
            superseded_row_ids: set[int] = set()
            missing_current_row_ids: list[int] = []
            if missing_jobs:
                entity_ids = sorted({job.entity_id for job in missing_jobs})
                source_rows_by_entity = await self._fetch_prepare_window_source_rows(
                    session,
                    entity_ids,
                )
                current_generations = {
                    (entity_id, record["chunk_key"], record["source_hash"])
                    for entity_id, source_rows in source_rows_by_entity.items()
                    for record in self._build_chunk_records(source_rows)
                }
                for job in missing_jobs:
                    generation = (job.entity_id, job.chunk_key, job.source_hash)
                    if generation in current_generations:
                        missing_current_row_ids.append(job.chunk_row_id)
                    else:
                        superseded_row_ids.add(job.chunk_row_id)

            if missing_current_row_ids:
                raise RuntimeError(
                    "Vector manifest rows disappeared before write: "
                    f"{sorted(missing_current_row_ids)}"
                )

            current_jobs: list[tuple[_PendingEmbeddingJob, list[float]]] = []
            for job, embedding in zip(jobs, embeddings, strict=True):
                row = rows_by_id.get(job.chunk_row_id)
                if row is None:
                    continue
                if int(row["entity_id"]) != job.entity_id or str(row["chunk_key"]) != job.chunk_key:
                    raise RuntimeError(
                        f"Vector manifest row identity changed before write: {job.chunk_row_id}"
                    )
                if str(row["source_hash"]) != job.source_hash:
                    superseded_row_ids.add(job.chunk_row_id)
                    continue
                current_jobs.append((job, embedding))
            if not current_jobs:
                return _EmbeddingPersistenceResult(superseded_row_ids=frozenset(superseded_row_ids))

            params: dict[str, object] = {}
            generation_predicates: list[str] = []
            records = [
                VectorRecord(
                    key=VectorKey(
                        entity_id=job.entity_id,
                        chunk_key=job.chunk_key,
                    ),
                    source_hash=job.source_hash,
                    values=tuple(embedding),
                )
                for job, embedding in current_jobs
            ]
            for index, (job, _embedding) in enumerate(current_jobs):
                params[f"row_id_{index}"] = job.chunk_row_id
                params[f"source_hash_{index}"] = job.source_hash
                generation_predicates.append(
                    f"(id = :row_id_{index} AND source_hash = :source_hash_{index})"
                )

            persistence = _EmbeddingPersistenceResult(
                persisted_row_ids=frozenset(job.chunk_row_id for job, _embedding in current_jobs),
                superseded_row_ids=frozenset(superseded_row_ids),
            )

            if lock_external_write:
                # Constraint: extension adapters use stable logical keys outside
                # the authoritative SQL database. Hold its manifest lock across
                # adapter I/O so a newer prepare cannot advance this generation
                # before the external write and ready transition complete.
                await self._semantic_vector_index.upsert(records)
                await self._mark_embedding_jobs_ready(
                    session,
                    params=params,
                    generation_predicates=generation_predicates,
                )
                await session.commit()
                return persistence

        # Built-in adapters share the authoritative database. They verify and lock
        # each record's source_hash inside the same transaction as their vector write.
        await self._semantic_vector_index.upsert(records)
        async with db.scoped_session(self.session_maker) as session:
            await self._mark_embedding_jobs_ready(
                session,
                params=params,
                generation_predicates=generation_predicates,
            )
            await session.commit()
        return persistence

    async def _mark_embedding_jobs_ready(
        self,
        session: AsyncSession,
        *,
        params: dict[str, object],
        generation_predicates: list[str],
    ) -> None:
        """Publish only the source generations written by the adapter."""
        await session.execute(
            text(
                "UPDATE search_vector_chunks SET embedding_status = 'ready', "
                f"updated_at = {self._timestamp_now_expr()} "
                "WHERE project_id = :project_id "
                "AND vector_index = :vector_index "
                "AND embedding_model = :embedding_model "
                "AND (" + " OR ".join(generation_predicates) + ")"
            ),
            {
                **params,
                "project_id": self.project_id,
                "vector_index": self._semantic_vector_index_name,
                "embedding_model": self._embedding_model_key(),
            },
        )

    async def _delete_entity_chunks(
        self,
        session: AsyncSession,
        entity_id: int,
        *,
        expected_deletions: Sequence[_StagedVectorDeletion] | None = None,
    ) -> list[_StagedVectorDeletion]:
        """Stage an entity deletion by making its manifest rows non-searchable."""
        return await self._stage_vector_deletions(
            session,
            entity_id=entity_id,
            expected_deletions=expected_deletions,
        )

    async def _delete_stale_chunks(
        self,
        session: AsyncSession,
        stale_ids: list[int],
        entity_id: int,
        *,
        expected_deletions: Sequence[_StagedVectorDeletion] | None = None,
    ) -> list[_StagedVectorDeletion]:
        """Stage stale chunk deletion by making manifest rows non-searchable."""
        if not stale_ids:
            return []
        return await self._stage_vector_deletions(
            session,
            entity_id=entity_id,
            row_ids=stale_ids,
            expected_deletions=expected_deletions,
        )

    async def _stage_vector_deletions(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        row_ids: Sequence[int] | None = None,
        expected_deletions: Sequence[_StagedVectorDeletion] | None = None,
    ) -> list[_StagedVectorDeletion]:
        """Durably stage and return the exact manifest generations to delete."""
        params: dict[str, object] = {
            "project_id": self.project_id,
            "entity_id": entity_id,
        }
        generation_clause = ""
        if expected_deletions is not None:
            if not expected_deletions:
                return []
            predicates: list[str] = []
            for index, deletion in enumerate(expected_deletions):
                params[f"row_id_{index}"] = deletion.row_id
                params[f"source_hash_{index}"] = deletion.source_hash
                params[f"vector_index_{index}"] = deletion.vector_index
                predicates.append(
                    f"(id = :row_id_{index} "
                    f"AND source_hash = :source_hash_{index} "
                    f"AND vector_index = :vector_index_{index})"
                )
            generation_clause = " AND (" + " OR ".join(predicates) + ")"
        elif row_ids is not None:
            if not row_ids:
                return []
            placeholders: list[str] = []
            for index, row_id in enumerate(row_ids):
                params[f"row_id_{index}"] = row_id
                placeholders.append(f":row_id_{index}")
            generation_clause = " AND id IN (" + ", ".join(placeholders) + ")"

        result = await session.execute(
            text(
                "UPDATE search_vector_chunks SET embedding_status = 'pending' "
                "WHERE project_id = :project_id AND entity_id = :entity_id"
                + generation_clause
                + " RETURNING id, chunk_key, source_hash, vector_index"
            ),
            params,
        )
        staged = [
            _StagedVectorDeletion(
                row_id=int(row["id"]),
                chunk_key=str(row["chunk_key"]),
                source_hash=str(row["source_hash"]),
                vector_index=str(row["vector_index"]),
            )
            for row in result.mappings().all()
        ]
        self._assert_manifest_vector_ownership(deletion.vector_index for deletion in staged)
        return staged

    async def _finalize_prepared_vector_deletions(
        self,
        prepared: _PreparedEntityVectorSync,
    ) -> None:
        """Delete staged adapter records, then remove their SQL manifest rows.

        The prepare transaction commits `pending` first. If an external delete
        fails, those rows remain non-searchable and the next sync retries the
        idempotent delete instead of losing cleanup intent.
        """
        if not prepared.staged_deletions:
            return
        if not hasattr(self, "_semantic_vector_index"):
            return

        params: dict[str, object] = {
            "project_id": self.project_id,
            "entity_id": prepared.entity_id,
        }
        predicates: list[str] = []
        for index, deletion in enumerate(prepared.staged_deletions):
            params[f"row_id_{index}"] = deletion.row_id
            params[f"source_hash_{index}"] = deletion.source_hash
            params[f"vector_index_{index}"] = deletion.vector_index
            predicates.append(
                f"(id = :row_id_{index} "
                f"AND source_hash = :source_hash_{index} "
                f"AND vector_index = :vector_index_{index})"
            )
        generation_clause = " OR ".join(predicates)
        deletions = [
            VectorDeletion(
                key=VectorKey(
                    entity_id=prepared.entity_id,
                    chunk_key=deletion.chunk_key,
                ),
                source_hash=deletion.source_hash,
            )
            for deletion in prepared.staged_deletions
        ]

        external_vector_index = self._semantic_vector_index_name not in _BUILT_IN_VECTOR_INDEX_NAMES
        if external_vector_index:
            async with db.scoped_session(self.session_maker) as session:
                connection = await session.connection()
                if connection.dialect.name == "sqlite":
                    lock_statement = (
                        "UPDATE search_vector_chunks SET source_hash = source_hash "
                        "WHERE project_id = :project_id AND entity_id = :entity_id "
                        "AND embedding_status = 'pending' AND ("
                        + generation_clause
                        + ") RETURNING id, chunk_key, source_hash, vector_index"
                    )
                else:
                    lock_statement = (
                        "SELECT id, chunk_key, source_hash, vector_index "
                        "FROM search_vector_chunks "
                        "WHERE project_id = :project_id AND entity_id = :entity_id "
                        "AND embedding_status = 'pending' AND ("
                        + generation_clause
                        + ") FOR UPDATE"
                    )
                result = await session.execute(text(lock_statement), params)
                rows = result.mappings().all()
                self._assert_manifest_vector_ownership(row["vector_index"] for row in rows)
                current_generations = {(int(row["id"]), str(row["source_hash"])) for row in rows}
                current_deletions = [
                    deletion
                    for deletion, staged in zip(
                        deletions,
                        prepared.staged_deletions,
                        strict=True,
                    )
                    if (staged.row_id, staged.source_hash) in current_generations
                ]
                if not current_deletions:
                    return
                await self._semantic_vector_index.delete(current_deletions)
                await session.execute(
                    text(
                        "DELETE FROM search_vector_chunks "
                        "WHERE project_id = :project_id AND entity_id = :entity_id "
                        "AND embedding_status = 'pending' AND (" + generation_clause + ")"
                    ),
                    params,
                )
                await session.commit()
            return

        await self._semantic_vector_index.delete(deletions)
        if self._semantic_vector_index_name in _BUILT_IN_VECTOR_INDEX_NAMES:
            return
        async with db.scoped_session(self.session_maker) as session:
            await session.execute(
                text(
                    "DELETE FROM search_vector_chunks "
                    "WHERE project_id = :project_id AND entity_id = :entity_id "
                    "AND embedding_status = 'pending' AND (" + generation_clause + ")"
                ),
                params,
            )
            await session.commit()

    @abstractmethod
    def _distance_to_similarity(self, distance: float) -> float:
        """Convert a backend-specific vector distance to cosine similarity in [0, 1].

        Backend-specific implementations:
        - SQLite (vec0): L2/Euclidean distance → cosine similarity via 1 - d²/2
        - Postgres (pgvector <=>): Cosine distance → cosine similarity via 1 - d
        """
        pass  # pragma: no cover

    # ------------------------------------------------------------------
    # Shared index / delete operations
    # ------------------------------------------------------------------

    async def purge_stale_search_rows(self) -> int:
        return await purge_stale_search_index_rows(self.session_maker, self.project_id)

    async def index_item(self, search_index_row: SearchIndexRow) -> None:
        """Index or update a single item.

        This implementation is shared across backends as it uses standard SQL INSERT.
        """

        async with db.scoped_session(self.session_maker) as session:
            # Delete existing record if any
            await session.execute(
                text(
                    "DELETE FROM search_index WHERE permalink = :permalink AND project_id = :project_id"
                ),
                {"permalink": search_index_row.permalink, "project_id": self.project_id},
            )

            # When using text() raw SQL, always serialize JSON to string
            # Both SQLite (TEXT) and Postgres (JSONB) accept JSON strings in raw SQL
            # The database driver/column type will handle conversion
            insert_data = search_index_row.to_insert(serialize_json=True)
            insert_data["project_id"] = self.project_id
            insert_data["script_ngrams"] = build_script_ngrams(
                search_index_row.title,
                search_index_row.content_stems,
                search_index_row.content_snippet,
            )

            # Insert new record
            await session.execute(
                text("""
                    INSERT INTO search_index (
                        id, title, content_stems, content_snippet, script_ngrams, permalink, file_path, type, metadata,
                        from_id, to_id, relation_type,
                        entity_id, category,
                        created_at, updated_at,
                        project_id
                    ) VALUES (
                        :id, :title, :content_stems, :content_snippet, :script_ngrams, :permalink, :file_path, :type, :metadata,
                        :from_id, :to_id, :relation_type,
                        :entity_id, :category,
                        :created_at, :updated_at,
                        :project_id
                    )
                """),
                insert_data,
            )
            logger.debug(f"indexed row {search_index_row}")
            await session.commit()

    async def bulk_index_items(self, search_index_rows: List[SearchIndexRow]) -> None:
        """Index multiple items in a single batch operation.

        This implementation is shared across backends as it uses standard SQL INSERT.

        Note: This method assumes that any existing records for the entity_id
        have already been deleted (typically via delete_by_entity_id).

        Args:
            search_index_rows: List of SearchIndexRow objects to index
        """

        if not search_index_rows:  # pragma: no cover
            return  # pragma: no cover

        async with db.scoped_session(self.session_maker) as session:
            # When using text() raw SQL, always serialize JSON to string
            # Both SQLite (TEXT) and Postgres (JSONB) accept JSON strings in raw SQL
            # The database driver/column type will handle conversion
            insert_data_list = []
            for row in search_index_rows:
                insert_data = row.to_insert(serialize_json=True)
                insert_data["project_id"] = self.project_id
                insert_data["script_ngrams"] = build_script_ngrams(
                    row.title,
                    row.content_stems,
                    row.content_snippet,
                )
                insert_data_list.append(insert_data)

            # Batch insert all records using executemany
            await session.execute(
                text("""
                    INSERT INTO search_index (
                        id, title, content_stems, content_snippet, script_ngrams, permalink, file_path, type, metadata,
                        from_id, to_id, relation_type,
                        entity_id, category,
                        created_at, updated_at,
                        project_id
                    ) VALUES (
                        :id, :title, :content_stems, :content_snippet, :script_ngrams, :permalink, :file_path, :type, :metadata,
                        :from_id, :to_id, :relation_type,
                        :entity_id, :category,
                        :created_at, :updated_at,
                        :project_id
                    )
                """),
                insert_data_list,
            )
            logger.debug(f"Bulk indexed {len(search_index_rows)} rows")
            await session.commit()

    async def get_entity_search_rows(self, entity_id: int) -> list[SearchIndexRow]:
        """Return every search projection owned by one entity."""
        async with db.scoped_session(self.session_maker) as session:
            result = await session.execute(
                text(
                    "SELECT project_id, id, title, content_stems, content_snippet, "
                    "permalink, file_path, type, metadata, from_id, to_id, relation_type, "
                    "entity_id, category, created_at, updated_at "
                    "FROM search_index "
                    "WHERE project_id = :project_id AND ("
                    "(type = 'entity' AND id = :entity_id) "
                    "OR entity_id = :entity_id "
                    "OR (type = 'relation' AND from_id = :entity_id)"
                    ") ORDER BY type, id"
                ),
                {"project_id": self.project_id, "entity_id": entity_id},
            )
            return [SearchIndexRow.from_mapping(dict(row)) for row in result.mappings().all()]

    async def get_entity_chunk_manifest(self, entity_id: int) -> list[ChunkManifestRow]:
        """Return the stored vector-chunk manifest for one project-scoped entity."""
        async with db.scoped_session(self.session_maker) as session:
            connection = await session.connection()
            manifest_exists = await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).has_table("search_vector_chunks")
            )
            if not manifest_exists:
                return []

            result = await session.execute(
                text(
                    "SELECT entity_id, chunk_key, chunk_text, source_hash, "
                    "entity_fingerprint, embedding_model, vector_index, "
                    "embedding_status, updated_at "
                    "FROM search_vector_chunks "
                    "WHERE project_id = :project_id AND entity_id = :entity_id "
                    "ORDER BY chunk_key"
                ),
                {"project_id": self.project_id, "entity_id": entity_id},
            )
            return [ChunkManifestRow.from_mapping(dict(row)) for row in result.mappings().all()]

    @abstractmethod
    async def get_entity_physical_chunk_keys(self, entity_id: int) -> set[str] | None:
        """Return manifest chunk keys whose built-in physical vector row is live.

        ``None`` means physical storage is not inspectable here: semantic search is
        disabled, or the configured index is external and exposes no portable
        storage-inspection contract (mirroring get_embedding_status()).
        """
        ...

    async def delete_by_entity_id(self, entity_id: int) -> None:
        """Delete all search index entries for an entity.

        This implementation is shared across backends as it uses standard SQL DELETE.
        """
        async with db.scoped_session(self.session_maker) as session:
            await session.execute(
                text(
                    "DELETE FROM search_index WHERE entity_id = :entity_id AND project_id = :project_id"
                ),
                {"entity_id": entity_id, "project_id": self.project_id},
            )
            await session.commit()

    async def delete_by_permalink(self, permalink: str) -> None:
        """Delete a search index entry by permalink.

        This implementation is shared across backends as it uses standard SQL DELETE.
        """
        async with db.scoped_session(self.session_maker) as session:
            await session.execute(
                text(
                    "DELETE FROM search_index WHERE permalink = :permalink AND project_id = :project_id"
                ),
                {"permalink": permalink, "project_id": self.project_id},
            )
            await session.commit()

    async def delete_project_search_rows(self) -> None:
        """Delete every full-text search row owned by this repository's project.

        Shared across backends: both store rows in `search_index` keyed by
        project_id, and a project-scoped DELETE leaves sibling projects in the
        same database untouched. On Postgres the bounded FTS chunk rows follow
        via their ON DELETE CASCADE foreign key.
        """
        async with db.scoped_session(self.session_maker) as session:
            await session.execute(
                text("DELETE FROM search_index WHERE project_id = :project_id"),
                {"project_id": self.project_id},
            )
            await session.commit()

    async def execute_query(
        self,
        query: Executable,
        params: Dict[str, Any],
    ) -> Result[Any]:
        """Execute a query asynchronously.

        This implementation is shared across backends for utility query execution.
        """
        async with db.scoped_session(self.session_maker) as session:
            start_time = time.perf_counter()
            result = await session.execute(query, params)
            end_time = time.perf_counter()
            elapsed_time = end_time - start_time
            logger.debug(f"Query executed successfully in {elapsed_time:.2f}s.")
            return result

    async def delete_entity_vector_rows(self, entity_id: int) -> None:
        """Delete one entity's derived vector rows using the backend's cleanup path."""
        await self._ensure_vector_tables()

        async with db.scoped_session(self.session_maker) as session:
            staged_deletions = await self._delete_entity_chunks(session, entity_id)
            await session.commit()
        await self._finalize_prepared_vector_deletions(
            _PreparedEntityVectorSync(
                entity_id=entity_id,
                sync_start=time.perf_counter(),
                source_rows_count=0,
                embedding_jobs=[],
                delete_entity_vectors=True,
                staged_deletions=staged_deletions,
            )
        )

    async def delete_external_entity_vectors(
        self,
        session: AsyncSession,
        entity_ids: Sequence[int],
    ) -> None:
        """Delete externally stored entity vectors under the caller's project lock."""
        deleted_entity_ids = tuple(dict.fromkeys(entity_ids))
        await self._lock_external_vector_write(session)
        if not deleted_entity_ids:
            return

        params = {
            "project_id": self.project_id,
            **{
                f"entity_id_{index}": entity_id
                for index, entity_id in enumerate(deleted_entity_ids)
            },
        }
        placeholders = ", ".join(f":entity_id_{index}" for index in range(len(deleted_entity_ids)))
        ownership_result = await session.execute(
            text(
                "SELECT DISTINCT vector_index FROM search_vector_chunks "
                "WHERE project_id = :project_id "
                f"AND entity_id IN ({placeholders})"
            ),
            params,
        )
        recorded_indexes = frozenset(
            str(vector_index) for vector_index in ownership_result.scalars().all()
        )
        await self._delete_external_entity_vectors_locked(
            session,
            deleted_entity_ids,
            recorded_indexes=recorded_indexes,
        )

    async def _delete_external_entity_vectors_locked(
        self,
        session: AsyncSession,
        entity_ids: Sequence[int],
        *,
        recorded_indexes: frozenset[str],
    ) -> None:
        """Delete external vectors after the caller has acquired the project lock."""
        self._assert_manifest_vector_ownership(recorded_indexes)
        external_indexes = recorded_indexes - _BUILT_IN_VECTOR_INDEX_NAMES
        if not external_indexes:
            return

        deleted_entity_ids = tuple(dict.fromkeys(entity_ids))
        configured_index = self._semantic_vector_index_name
        if not hasattr(self, "_semantic_vector_index"):
            raise SemanticVectorIndexExtensionError(
                f"Semantic vector adapter {configured_index!r} is unavailable. "
                "Enable semantic search and retry the entity deletion."
            )

        # Trigger: DB-first deletion runs inside a caller-owned SQL transaction.
        # Why: the external adapter cannot participate in that transaction. If its
        # delete succeeds and the caller later rolls back, ready manifests would
        # incorrectly claim the now-missing vectors are searchable.
        # Outcome: commit a non-searchable retry marker in an independent session
        # before touching external storage while the caller retains the project
        # lock; a retried delete remains idempotent and no new generation can race.
        stage_params = {
            "project_id": self.project_id,
            "vector_index": configured_index,
            **{
                f"entity_id_{index}": entity_id
                for index, entity_id in enumerate(deleted_entity_ids)
            },
        }
        placeholders = ", ".join(f":entity_id_{index}" for index in range(len(deleted_entity_ids)))
        connection = await session.connection()
        if connection.dialect.name == "postgresql":
            async with db.scoped_session(self.session_maker) as marker_session:
                await marker_session.execute(
                    text(
                        "UPDATE search_vector_chunks SET embedding_status = 'pending' "
                        "WHERE project_id = :project_id AND vector_index = :vector_index "
                        f"AND entity_id IN ({placeholders})"
                    ),
                    stage_params,
                )
                await marker_session.commit()
        else:
            # SQLite permits only one writer, so a second marker transaction would
            # deadlock behind the project write lock. Keep the marker in the caller
            # transaction; extension cleanup still remains serialized.
            await session.execute(
                text(
                    "UPDATE search_vector_chunks SET embedding_status = 'pending' "
                    "WHERE project_id = :project_id AND vector_index = :vector_index "
                    f"AND entity_id IN ({placeholders})"
                ),
                stage_params,
            )

        await self._semantic_vector_index.initialize()
        for entity_id in deleted_entity_ids:
            await self._semantic_vector_index.delete_entity(entity_id)

    async def _delete_project_builtin_vector_rows(self, session: AsyncSession) -> None:
        """Delete backend-owned vector rows before their SQL manifest is removed."""

    async def _lock_external_vector_project(
        self,
        session: AsyncSession,
        *,
        dialect_name: str,
    ) -> None:
        """Serialize project-wide cleanup with external adapter writes."""
        if dialect_name == "postgresql":
            await session.execute(
                text("SELECT id FROM project WHERE id = :project_id FOR UPDATE"),
                {"project_id": self.project_id},
            )
            return
        if dialect_name == "sqlite":
            # SQLite has no row-level lock. This no-op write acquires its database
            # write lock before ownership is read and keeps it through adapter I/O.
            await session.execute(
                text("UPDATE project SET id = id WHERE id = :project_id"),
                {"project_id": self.project_id},
            )
            return
        raise SemanticVectorIndexExtensionError(
            f"External vector cleanup does not support SQL dialect {dialect_name!r}."
        )

    async def _lock_external_vector_write(self, session: AsyncSession) -> None:
        """Share one project lock across external manifest mutations and cleanup."""
        if not self._uses_external_vector_index():
            return

        connection = await session.connection()
        await self._lock_external_vector_project(
            session,
            dialect_name=connection.dialect.name,
        )

    def _uses_external_vector_index(self) -> bool:
        """Return whether this repository writes vectors outside the SQL backend."""
        return self._semantic_vector_index_name not in _BUILT_IN_VECTOR_INDEX_NAMES and hasattr(
            self, "_semantic_vector_index"
        )

    def _assert_manifest_vector_ownership(self, vector_index_names: Iterable[object]) -> None:
        """Reject cleanup that cannot reach every externally owned vector."""
        recorded_indexes = frozenset(str(name) for name in vector_index_names if str(name))
        external_indexes = recorded_indexes - _BUILT_IN_VECTOR_INDEX_NAMES
        configured_index = self._semantic_vector_index_name
        if external_indexes and (
            not hasattr(self, "_semantic_vector_index")
            or external_indexes != frozenset({configured_index})
        ):
            raise SemanticVectorIndexExtensionError(
                "Cannot mutate vector manifests owned by external indexes "
                f"{sorted(external_indexes)!r} with configured adapter "
                f"{configured_index!r}. Restore the owning adapter and retry."
            )

    async def delete_project_vector_rows(
        self,
        *,
        strict_adapter_cleanup: bool = True,
        session: AsyncSession | None = None,
    ) -> None:
        """Delete this project's vectors through the configured storage adapter.

        Core enumerates ownership from the SQL manifest because the adapter
        contract intentionally has no project-wide listing or destructive reset.
        A full reindex clears the manifest even when semantic search is disabled,
        so stale ready rows cannot become current if the feature is re-enabled.
        External adapter cleanup is strict by default because the manifest is the
        only durable ownership record for remote vectors. Ownership mismatches
        fail closed because only the owning extension can safely remove previously
        written vectors.
        """
        if session is not None:
            await self._delete_project_vector_rows_in_session(
                session,
                strict_adapter_cleanup=strict_adapter_cleanup,
            )
            return

        async with db.scoped_session(self.session_maker) as owned_session:
            changed = await self._delete_project_vector_rows_in_session(
                owned_session,
                strict_adapter_cleanup=strict_adapter_cleanup,
            )
            if changed:
                await owned_session.commit()

    async def _delete_project_vector_rows_in_session(
        self,
        session: AsyncSession,
        *,
        strict_adapter_cleanup: bool,
    ) -> bool:
        """Delete project vectors while retaining the caller's transaction boundary."""
        connection = await session.connection()
        manifest_exists = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).has_table("search_vector_chunks")
        )
        if not manifest_exists:
            return False

        manifest_columns = await connection.run_sync(
            lambda sync_connection: {
                str(column["name"])
                for column in inspect(sync_connection).get_columns("search_vector_chunks")
            }
        )
        manifest_has_embedding_status = "embedding_status" in manifest_columns
        configured_index = self._semantic_vector_index_name
        external_adapter_available = (
            configured_index not in _BUILT_IN_VECTOR_INDEX_NAMES
            and hasattr(self, "_semantic_vector_index")
        )
        if external_adapter_available:
            # Constraint: the project row is the only lock that also covers future
            # manifest inserts. The caller retains it through adapter I/O, manifest
            # removal, and—during hard deletion—the project-row delete itself.
            await self._lock_external_vector_write(session)

        entity_ids_by_vector_index: dict[str, list[int]] = {}
        if "vector_index" in manifest_columns:
            result = await session.execute(
                text(
                    "SELECT DISTINCT entity_id, vector_index FROM search_vector_chunks "
                    "WHERE project_id = :project_id ORDER BY vector_index, entity_id"
                ),
                {"project_id": self.project_id},
            )
            for entity_id, vector_index in result.all():
                entity_ids_by_vector_index.setdefault(str(vector_index), []).append(int(entity_id))
        else:
            result = await session.execute(
                text(
                    "SELECT DISTINCT entity_id FROM search_vector_chunks "
                    "WHERE project_id = :project_id ORDER BY entity_id"
                ),
                {"project_id": self.project_id},
            )
            legacy_vector_index = (
                "sqlite-vec" if connection.dialect.name == "sqlite" else "pgvector"
            )
            entity_ids_by_vector_index[legacy_vector_index] = [
                int(entity_id) for entity_id in result.scalars().all()
            ]

        # Trigger: manifests belong to an external index other than the available adapter.
        # Why: adapter configuration can change after vectors were written, and deleting
        # the manifest would discard the only durable routing information for old vectors.
        # Outcome: fail before touching any adapter or manifest so the owner can be restored.
        self._assert_manifest_vector_ownership(entity_ids_by_vector_index)

        builtin_indexes = frozenset(entity_ids_by_vector_index) & _BUILT_IN_VECTOR_INDEX_NAMES
        if manifest_has_embedding_status and (not external_adapter_available or builtin_indexes):
            builtin_filter = ""
            if external_adapter_available:
                builtin_filter = " AND vector_index IN ('pgvector', 'sqlite-vec')"
            await session.execute(
                text(
                    "UPDATE search_vector_chunks SET embedding_status = 'pending' "
                    f"WHERE project_id = :project_id{builtin_filter}"
                ),
                {"project_id": self.project_id},
            )

        adapter_entity_ids = entity_ids_by_vector_index.get(configured_index, [])
        if external_adapter_available:
            try:
                await self._delete_external_entity_vectors_locked(
                    session,
                    adapter_entity_ids,
                    recorded_indexes=frozenset(entity_ids_by_vector_index),
                )
            except Exception as exc:
                # Trigger: a configured external adapter cannot initialize or delete.
                # Why: reindex and project deletion must not discard the only
                # ownership manifest for external data.
                # Outcome: strict callers stop for a retry before manifest deletion.
                logger.warning(
                    "Could not clean semantic vector adapter: "
                    "project_id={project_id} vector_index={vector_index} error={error}",
                    project_id=self.project_id,
                    vector_index=self._semantic_vector_index_name,
                    error=exc,
                )
                if strict_adapter_cleanup:
                    raise

        await self._delete_project_builtin_vector_rows(session)
        await session.execute(
            text("DELETE FROM search_vector_chunks WHERE project_id = :project_id"),
            {"project_id": self.project_id},
        )
        return True

    async def delete_stale_vector_rows(self) -> None:
        """Delete vectors whose source entity no longer exists.

        The SQL manifest remains the source of truth for ownership. External
        indexes receive stable entity deletes before their manifest rows are
        removed, avoiding backend-specific cleanup in the service layer.
        """
        if not self._semantic_enabled:
            return

        await self._ensure_vector_tables()
        async with db.scoped_session(self.session_maker) as session:
            result = await session.execute(
                text(
                    "SELECT DISTINCT entity_id FROM search_vector_chunks "
                    "WHERE project_id = :project_id AND entity_id NOT IN ("
                    "SELECT id FROM entity WHERE project_id = :project_id) "
                    "ORDER BY entity_id"
                ),
                {"project_id": self.project_id},
            )
            entity_ids = [int(entity_id) for entity_id in result.scalars().all()]

        for entity_id in entity_ids:
            await self.delete_entity_vector_rows(entity_id)

    async def reconcile_vector_index(self) -> None:
        """Let capable adapters prune records absent from the ready SQL manifest."""
        if not self._semantic_enabled:
            return

        await self._ensure_vector_tables()
        if not isinstance(self._semantic_vector_index, SemanticVectorIndexReconciler):
            return

        external_vector_index = self._uses_external_vector_index()
        async with db.scoped_session(self.session_maker) as session:
            if external_vector_index:
                # Trigger: reconciliation snapshots the live manifest before asking
                # an external adapter to delete everything else.
                # Why: a concurrent watcher could otherwise publish a new vector
                # after the snapshot and have reconciliation delete that live key.
                # Outcome: share the project lock with external writes through both
                # the manifest read and orphan deletion.
                await self._lock_external_vector_write(session)
            result = await session.execute(
                text(
                    "SELECT entity_id, chunk_key FROM search_vector_chunks "
                    "WHERE project_id = :project_id "
                    "AND vector_index = :vector_index "
                    "AND embedding_model = :embedding_model "
                    "AND embedding_status = 'ready' "
                    "ORDER BY entity_id, chunk_key"
                ),
                {
                    "project_id": self.project_id,
                    "vector_index": self._semantic_vector_index_name,
                    "embedding_model": self._embedding_model_key(),
                },
            )
            live_keys = [
                VectorKey(
                    entity_id=int(row["entity_id"]),
                    chunk_key=str(row["chunk_key"]),
                )
                for row in result.mappings().all()
            ]

            if external_vector_index:
                await self._semantic_vector_index.delete_orphans(live_keys)
                await session.commit()
                return

        await self._semantic_vector_index.delete_orphans(live_keys)

    # ------------------------------------------------------------------
    # Shared semantic search: guard, text processing, chunking
    # ------------------------------------------------------------------

    def _assert_semantic_available(self) -> None:
        if not self._semantic_enabled:
            raise SemanticSearchDisabledError(
                "Semantic search is disabled. Set BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true."
            )
        if self._embedding_provider is None:
            raise SemanticDependenciesMissingError(
                "No embedding provider configured. "
                "Install/update basic-memory to include semantic dependencies "
                "(pip install -U basic-memory) "
                "and set semantic_search_enabled=true."
            )

    def _build_chunk_records(self, rows: Iterable[SemanticSourceRow]) -> list[VectorChunkRecord]:
        chunk_build = build_vector_chunk_records(rows)
        if chunk_build.duplicate_chunk_keys:
            logger.warning(
                "Collapsed duplicate vector chunk keys before embedding sync: "
                "project_id={project_id} duplicate_chunk_keys={duplicate_chunk_keys}",
                project_id=self.project_id,
                duplicate_chunk_keys=chunk_build.duplicate_chunk_keys,
            )

        return chunk_build.records

    def _build_entity_fingerprint(self, chunk_records: list[VectorChunkRecord]) -> str:
        return build_entity_fingerprint(chunk_records)

    def _embedding_model_key(self) -> str:
        """Build a stable model identity for vector invalidation checks."""
        assert self._embedding_provider is not None
        provider = self._embedding_provider
        # Trigger: providers can change request/input semantics without changing
        # model/dimensions.
        # Why: asymmetric providers may use role-specific API params or literal
        # text-prefix transforms that change stored vector meaning.
        # Outcome: reindex treats those semantic config changes as stale vectors.
        provider_identity = embedding_provider_identity(provider)
        return f"{type(provider).__name__}:{provider_identity}"

    def _plan_entity_vector_shard(
        self,
        pending_records: list[VectorChunkRecord],
    ) -> _EntityVectorShardPlan:
        """Select the bounded shard to process for one entity sync invocation."""
        return semantic_vector_sync.plan_entity_vector_shard(
            pending_records,
            shard_size=OVERSIZED_ENTITY_VECTOR_SHARD_SIZE,
        )

    def _log_vector_shard_plan(
        self,
        *,
        entity_id: int,
        shard_plan: _EntityVectorShardPlan,
    ) -> None:
        """Emit shard planning logs once the pending work is known."""
        semantic_vector_sync.log_vector_shard_plan(
            self,
            entity_id=entity_id,
            shard_plan=shard_plan,
            shard_size=OVERSIZED_ENTITY_VECTOR_SHARD_SIZE,
        )

    # --- Text splitting ---

    def _split_text_into_chunks(self, text_value: str) -> list[str]:
        return split_text_into_chunks(text_value)

    # ------------------------------------------------------------------
    # Shared semantic search: sync_entity_vectors orchestration
    # ------------------------------------------------------------------

    async def sync_entity_vectors(self, entity_id: int) -> None:
        """Sync semantic chunk rows + embeddings for a single entity."""
        await self._sync_entity_vectors_internal(
            [entity_id],
            progress_callback=None,
            continue_on_error=False,
        )

    async def sync_entity_vectors_batch(
        self,
        entity_ids: list[int],
        progress_callback: Optional[Callable[[int, int, int], Any]] = None,
    ) -> VectorSyncBatchResult:
        """Sync semantic chunk rows + embeddings for a batch of entities."""
        result = await self._sync_entity_vectors_internal(
            entity_ids,
            progress_callback=progress_callback,
            continue_on_error=True,
        )
        # Recorded here rather than inside the portable sync helper: this is the
        # boundary that owns a database session, and the helper is also driven by
        # test doubles that have none. The terminal states it reports are the
        # sharding rule's own, so readiness still cannot drift from it.
        await self.record_entity_vector_deferrals(
            deferred_entity_ids=set(result.deferred_entity_ids),
            completed_entity_ids=set(result.synced_entity_ids),
        )
        return result

    async def _sync_entity_vectors_internal(
        self,
        entity_ids: list[int],
        progress_callback: Optional[Callable[[int, int, int], Any]],
        continue_on_error: bool,
    ) -> VectorSyncBatchResult:
        """Run shared vector sync orchestration for one or many entities."""
        return await semantic_vector_sync.sync_entity_vectors_internal(
            self,
            entity_ids,
            progress_callback,
            continue_on_error,
        )

    def _vector_prepare_window_size(self) -> int:
        """Return the number of entities to prepare in one orchestration window."""
        return semantic_vector_sync.vector_prepare_window_size(
            self,
            max_window_size=_SQLITE_MAX_PREPARE_WINDOW,
        )

    @asynccontextmanager
    async def _prepare_entity_write_scope(self):
        """Serialize the write-side prepare section when a backend needs it."""
        yield

    def _prepare_window_entity_params(
        self,
        entity_ids: list[int],
    ) -> tuple[str, dict[str, object]]:
        """Build deterministic bind params for one prepare window."""
        return semantic_vector_sync.prepare_window_entity_params(self, entity_ids)

    async def _fetch_prepare_window_source_rows(
        self,
        session: AsyncSession,
        entity_ids: list[int],
    ) -> dict[int, list[Any]]:
        """Fetch all search_index rows needed for one prepare window."""
        return await semantic_vector_sync.fetch_prepare_window_source_rows(
            self,
            session,
            entity_ids,
        )

    def _prepare_window_existing_rows_sql(self, placeholders: str) -> str:
        """SQL for existing chunk/embedding rows in one prepare window."""
        return semantic_vector_sync.prepare_window_existing_rows_sql(placeholders)

    async def _fetch_prepare_window_existing_rows(
        self,
        session: AsyncSession,
        entity_ids: list[int],
    ) -> dict[int, list[VectorChunkState]]:
        """Fetch all persisted chunk state needed for one prepare window."""
        return await semantic_vector_sync.fetch_prepare_window_existing_rows(
            self,
            session,
            entity_ids,
        )

    async def _prepare_entity_vector_jobs_window(
        self,
        entity_ids: list[int],
    ) -> list[_PreparedEntityVectorSync | BaseException]:
        """Prepare one window of entity vector jobs with shared read-side batching."""
        return await semantic_vector_sync.prepare_entity_vector_jobs_window(
            self,
            entity_ids,
        )

    async def _upsert_scheduled_chunk_records(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        scheduled_records: list[VectorChunkRecord],
        existing_by_key: dict[str, VectorChunkState],
        entity_fingerprint: str,
        embedding_model: str,
    ) -> list[_PendingEmbeddingJob]:
        """Upsert scheduled chunk rows and return embedding jobs."""
        return await semantic_vector_sync.upsert_scheduled_chunk_records(
            self,
            session,
            entity_id=entity_id,
            scheduled_records=scheduled_records,
            existing_by_key=existing_by_key,
            entity_fingerprint=entity_fingerprint,
            embedding_model=embedding_model,
        )

    async def _flush_embedding_jobs(
        self,
        flush_jobs: list[_PendingEmbeddingJob],
        entity_runtime: dict[int, _EntitySyncRuntime],
        synced_entity_ids: set[int],
    ) -> tuple[float, float]:
        """Embed and persist one queued flush chunk."""
        return await semantic_vector_sync.flush_embedding_jobs(
            self,
            flush_jobs,
            entity_runtime,
            synced_entity_ids,
        )

    def _finalize_completed_entity_syncs(
        self,
        *,
        entity_runtime: dict[int, _EntitySyncRuntime],
        synced_entity_ids: set[int],
        deferred_entity_ids: set[int],
        progress_callback: Callable[[int], None] | None = None,
    ) -> float:
        """Finalize completed entities and return cumulative queue wait seconds."""
        return semantic_vector_sync.finalize_completed_entity_syncs(
            self,
            entity_runtime=entity_runtime,
            synced_entity_ids=synced_entity_ids,
            deferred_entity_ids=deferred_entity_ids,
            progress_callback=progress_callback,
        )

    def _log_vector_sync_runtime_settings(
        self,
        *,
        backend_name: str,
        entities_total: int,
    ) -> None:
        """Log the resolved embedding runtime knobs before the first prepare window."""
        semantic_vector_sync.log_vector_sync_runtime_settings(
            self,
            backend_name=backend_name,
            entities_total=entities_total,
        )

    def _log_vector_sync_complete(
        self,
        *,
        entity_id: int,
        total_seconds: float,
        prepare_seconds: float,
        queue_wait_seconds: float,
        embed_seconds: float,
        write_seconds: float,
        source_rows_count: int,
        chunks_total: int,
        chunks_skipped: int,
        embedding_jobs_count: int,
        entity_skipped: bool,
        entity_complete: bool,
        oversized_entity: bool,
        pending_jobs_total: int,
        shard_index: int,
        shard_count: int,
        remaining_jobs_after_shard: int,
    ) -> None:
        """Log completion and slow-entity warnings with a consistent format."""
        semantic_vector_sync.log_vector_sync_complete(
            self,
            entity_id=entity_id,
            total_seconds=total_seconds,
            prepare_seconds=prepare_seconds,
            queue_wait_seconds=queue_wait_seconds,
            embed_seconds=embed_seconds,
            write_seconds=write_seconds,
            source_rows_count=source_rows_count,
            chunks_total=chunks_total,
            chunks_skipped=chunks_skipped,
            embedding_jobs_count=embedding_jobs_count,
            entity_skipped=entity_skipped,
            entity_complete=entity_complete,
            oversized_entity=oversized_entity,
            pending_jobs_total=pending_jobs_total,
            shard_index=shard_index,
            shard_count=shard_count,
            remaining_jobs_after_shard=remaining_jobs_after_shard,
        )

    async def _prepare_vector_session(self, session: AsyncSession) -> None:
        """Hook for per-session setup (e.g. loading sqlite-vec extension).

        Default implementation is a no-op. SQLite overrides this.
        """
        pass

    def _timestamp_now_expr(self) -> str:
        """SQL expression for 'now' in the backend.

        SQLite uses CURRENT_TIMESTAMP, Postgres uses NOW().
        """
        return "CURRENT_TIMESTAMP"

    # ------------------------------------------------------------------
    # Shared semantic search: retrieval mode dispatch
    # ------------------------------------------------------------------

    def _check_vector_eligible(
        self,
        search_text: Optional[str],
        permalink: Optional[str],
        permalink_match: Optional[str],
        title: Optional[str],
    ) -> bool:
        """Check whether search_text allows vector / hybrid retrieval."""
        return (
            bool(search_text)
            and bool(search_text.strip())
            and search_text.strip() != "*"
            and not permalink
            and not permalink_match
            and not title
        )

    async def _dispatch_retrieval_mode(
        self,
        *,
        search_text: Optional[str],
        permalink: Optional[str],
        permalink_match: Optional[str],
        title: Optional[str],
        note_types: Optional[List[str]],
        after_date: Optional[datetime],
        search_item_types: Optional[List[SearchItemType]],
        categories: Optional[List[str]],
        metadata_filters: Optional[dict[str, Any]],
        file_path_prefix: Optional[str],
        temporal: Optional[TemporalFilter],
        retrieval_mode: SearchRetrievalMode,
        min_similarity: Optional[float] = None,
        limit: int,
        offset: int,
        trace: SearchTraceCollector | None = None,
    ) -> Optional[List[SearchIndexRow]]:
        """Dispatch vector or hybrid retrieval if requested.

        Returns None when the mode is FTS so the caller should continue
        with its backend-specific FTS query.
        """
        mode = (
            retrieval_mode.value
            if isinstance(retrieval_mode, SearchRetrievalMode)
            else str(retrieval_mode)
        )
        can_use_vector = self._check_vector_eligible(search_text, permalink, permalink_match, title)
        search_text_value = search_text or ""

        if mode == SearchRetrievalMode.VECTOR.value:
            if not can_use_vector:
                raise ValueError(
                    "Vector retrieval requires a non-empty text query and does not support "
                    "title/permalink-only searches."
                )
            return await self._search_vector_only(
                search_text=search_text_value,
                permalink=permalink,
                permalink_match=permalink_match,
                title=title,
                note_types=note_types,
                after_date=after_date,
                search_item_types=search_item_types,
                categories=categories,
                metadata_filters=metadata_filters,
                file_path_prefix=file_path_prefix,
                temporal=temporal,
                min_similarity=min_similarity,
                limit=limit,
                offset=offset,
                trace=trace,
            )
        if mode == SearchRetrievalMode.HYBRID.value:
            if not can_use_vector:
                raise ValueError(
                    "Hybrid retrieval requires a non-empty text query and does not support "
                    "title/permalink-only searches."
                )
            return await self._search_hybrid(
                search_text=search_text_value,
                permalink=permalink,
                permalink_match=permalink_match,
                title=title,
                note_types=note_types,
                after_date=after_date,
                search_item_types=search_item_types,
                categories=categories,
                metadata_filters=metadata_filters,
                file_path_prefix=file_path_prefix,
                temporal=temporal,
                min_similarity=min_similarity,
                limit=limit,
                offset=offset,
                trace=trace,
            )

        # FTS mode: return None to let the subclass handle it
        return None

    # ------------------------------------------------------------------
    # Shared semantic search: vector-only retrieval
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_chunk_key(chunk_key: str) -> SearchIndexKey:
        """Parse a chunk_key like 'observation:5:0' into (type, search_index_id)."""
        parts = chunk_key.split(":")
        return parts[0], int(parts[1])

    # ------------------------------------------------------------------
    # Shared semantic search: cross-encoder reranking
    # ------------------------------------------------------------------

    def _should_rerank(self, query_text: str) -> bool:
        """Return whether a configured reranker should run for this query."""
        return self._rerank_provider is not None and bool(query_text)

    def _rerank_candidate_limit(self) -> int:
        """Return the fixed chunk window that owns reranker-prefix membership."""
        return max(
            self._semantic_vector_k,
            self._reranker_candidates * RERANK_POOL_CHUNK_FANOUT,
        )

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
        if self._should_rerank(query_text):
            # Trigger: the requested window extends beyond the fixed reranked prefix.
            # Why: a bounded prefix alone can under-fill large pages and hide the
            # semantic pagination probe even when more matches exist.
            # Outcome: keep prefix membership fixed while adding chunk headroom only
            # for the untouched tail that this request must return.
            rerank_candidate_limit = self._rerank_candidate_limit()
            tail_size = max(0, limit + offset - self._reranker_candidates)
            return rerank_candidate_limit + tail_size * 10
        return max(self._semantic_vector_k, (limit + offset) * 10)

    def _rerank_document_text(self, row: SearchIndexRow) -> str:
        """Build the document text handed to the cross-encoder for one candidate.

        Prefer the matched chunk (the most relevant passage of a large note),
        falling back to the stored snippet.
        """
        body = row.matched_chunk_text or row.content_snippet or ""
        return build_rerank_document(row.title, body, self._reranker_max_document_chars)

    @staticmethod
    def _demote_tail(tail: list[SearchIndexRow], floor: float) -> list[SearchIndexRow]:
        """Rescore un-reranked tail rows at or below the floor, preserving their order.

        The reranked pool carries [0, 1] relevance scores while the tail still holds
        raw retrieval scores on a different scale ([0, 1.3] for fused hybrid). Left as
        is, a tail row could outrank a reranked row numerically. Positive floors put
        the tail strictly below the pool; a zero floor yields zeroes because no smaller
        score exists in the public [0, 1] range. The returned pool-plus-tail sequence,
        rather than a later score-only sort, owns that tie-breaking invariant.
        """
        return [
            replace(row, score=score)
            for row, score in zip(tail, demote_tail_scores(floor, len(tail)))
        ]

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
        if self._rerank_provider is None or not query_text:
            return rows[offset:page_end]

        # Trigger: pagination needs more rows than the fixed rerank retrieval window.
        # Why: an expanded retrieval may introduce or strengthen raw candidates, but
        # letting them replace the original prefix causes duplicates and skips.
        # Outcome: the fixed window owns prefix membership; the expanded result only
        # supplies new, de-duplicated tail rows.
        pool_source = stable_rows if stable_rows is not None else rows
        pool = pool_source[: self._reranker_candidates]
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
        documents = [self._rerank_document_text(row) for row in pool]
        # A transient provider failure must surface instead of switching this page
        # back to retrieval order. A prior page may already have returned reranked
        # order, so degrading here can duplicate one result and omit another.
        rerank_start = time.perf_counter() if trace is not None else None
        scores = validate_rerank_scores(
            await self._rerank_provider.rerank(query_text, documents),
            len(pool),
        )

        order = sorted(range(len(pool)), key=lambda i: scores[i], reverse=True)
        reranked = [replace(pool[i], score=scores[i]) for i in order]
        logger.debug(
            "Reranked candidates: pool={pool} model={model}",
            pool=len(pool),
            model=self._rerank_provider.model_name,
        )
        tail_floor = reranked[-1].score or 0.0
        demoted_tail = self._demote_tail(tail, floor=tail_floor)
        reranked_rows = reranked + demoted_tail
        if trace is not None:
            assert pre_rerank_scores is not None and rerank_start is not None
            trace.rerank = build_rerank_stage(
                provider_model=self._rerank_provider.model_name,
                reranker_candidates=self._reranker_candidates,
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

    async def _search_vector_only(
        self,
        *,
        search_text: str,
        permalink: Optional[str],
        permalink_match: Optional[str],
        title: Optional[str],
        note_types: Optional[List[str]],
        after_date: Optional[datetime],
        search_item_types: Optional[List[SearchItemType]],
        categories: Optional[List[str]],
        metadata_filters: Optional[dict[str, Any]],
        file_path_prefix: Optional[str],
        temporal: Optional[TemporalFilter],
        min_similarity: Optional[float] = None,
        limit: int,
        offset: int,
        candidate_limit: int | None = None,
        _emit_observability_log: bool = True,
        _apply_rerank: bool = True,
        trace: SearchTraceCollector | None = None,
    ) -> List[SearchIndexRow]:
        """Run vector-only search returning chunk-level results.

        Returns individual search_index rows (entities, observations, relations)
        ranked by vector similarity. Each observation or relation is a first-class
        result, not collapsed into its parent entity.

        ``candidate_limit`` is supplied only by a composed retrieval stage that
        already sized the shared candidate pool.
        """
        self._assert_semantic_available()
        await self._ensure_vector_tables()
        assert self._embedding_provider is not None
        query_text = search_text.strip()
        if candidate_limit is None:
            candidate_limit = self._candidate_limit(limit, offset, query_text)
        query_start = time.perf_counter()
        embed_start = time.perf_counter()
        query_embedding = await self._embedding_provider.embed_query(query_text)
        embed_ms = (time.perf_counter() - embed_start) * 1000
        vector_query_start = time.perf_counter()

        if hasattr(self, "_semantic_vector_index"):
            # Constraint: vector adapters may open their own session, while the SQLite
            # test/runtime pool can contain only one connection. A plain AsyncSession
            # defers checkout until hydration runs after adapter search has released it.
            async with self.session_maker() as session:
                if trace is None:
                    vector_rows = await self._run_vector_query(
                        session,
                        query_embedding,
                        candidate_limit,
                    )
                else:
                    vector_rows = await self._run_vector_query(
                        session,
                        query_embedding,
                        candidate_limit,
                        trace=trace,
                    )
        else:
            # Compatibility for focused test repositories that implement the
            # pre-extension private query hook without configuring an adapter.
            async with db.scoped_session(self.session_maker) as session:
                await self._prepare_vector_session(session)
                if trace is None:
                    vector_rows = await self._run_vector_query(
                        session,
                        query_embedding,
                        candidate_limit,
                    )
                else:
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
                    min_similarity if min_similarity is not None else self._semantic_min_similarity
                ),
                min_similarity_source=("query" if min_similarity is not None else "config"),
                embed_ms=embed_ms,
                vector_query_ms=vector_query_ms,
            )

        def _log_vector_summary() -> None:
            if not _emit_observability_log:
                return

            total_ms = (time.perf_counter() - query_start) * 1000
            if total_ms > 2000:
                logger.warning(
                    "[SEMANTIC_SLOW_QUERY] Semantic query timing: project_id={project_id} "
                    "retrieval_mode={retrieval_mode} query_length={query_length} "
                    "candidate_limit={candidate_limit} vector_row_count={vector_row_count} "
                    "embed_ms={embed_ms:.2f} vector_query_ms={vector_query_ms:.2f} "
                    "hydrate_ms={hydrate_ms:.2f} total_ms={total_ms:.2f}",
                    project_id=self.project_id,
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
        for row in vector_rows:
            chunk_key = row.get("chunk_key", "")
            if "best_similarity" in row:
                similarity = float(row["best_similarity"])
            else:
                # Compatibility: private test doubles may still return native distance.
                distance = float(row["best_distance"])
                similarity = self._distance_to_similarity(distance)
            chunk_text = row.get("chunk_text", "")
            try:
                si_key = self._parse_chunk_key(chunk_key)
            except (ValueError, IndexError):
                # Fallback: group by entity_id for chunks without parseable keys
                continue
            current = similarity_by_si_key.get(si_key)
            if current is None or similarity > current:
                similarity_by_si_key[si_key] = similarity
            chunks_by_si_key.setdefault(si_key, []).append((similarity, chunk_text))

        if not similarity_by_si_key:
            hydrate_ms = (time.perf_counter() - hydrate_start) * 1000
            _log_vector_summary()
            return []

        # Filter out results below the minimum similarity threshold.
        # Per-query min_similarity overrides the instance-level default.
        effective_min_similarity = (
            min_similarity if min_similarity is not None else self._semantic_min_similarity
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

        # Apply optional filters if requested
        filter_requested = any(
            [
                permalink,
                permalink_match,
                title,
                note_types,
                after_date,
                search_item_types,
                categories,
                metadata_filters,
                file_path_prefix,
                temporal,
            ]
        )

        if filter_requested:
            filtered_rows = await self.search(
                search_text=None,
                permalink=permalink,
                permalink_match=permalink_match,
                title=title,
                note_types=note_types,
                after_date=after_date,
                search_item_types=search_item_types,
                categories=categories,
                metadata_filters=metadata_filters,
                file_path_prefix=file_path_prefix,
                temporal=temporal,
                retrieval_mode=SearchRetrievalMode.FTS,
                limit=VECTOR_FILTER_SCAN_LIMIT,
                offset=0,
            )
            # Use (type, id) tuples to avoid collisions between different
            # search_index row types that share the same auto-increment id.
            allowed_keys = {(row.type, row.id) for row in filtered_rows if row.id is not None}
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
                top_texts = [text for _, text in si_chunks[:TOP_CHUNKS_PER_RESULT]]
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
        # hybrid calls this internally (_apply_rerank=False) — hybrid reranks its own
        # fused result; _rerank_and_paginate no-ops back to a plain slice otherwise.
        if _apply_rerank:
            stable_rows = ranked_rows
            if self._should_rerank(query_text):
                stable_candidate_limit = self._rerank_candidate_limit()
                if candidate_limit > stable_candidate_limit:
                    if trace is not None:
                        trace.stable_pool_refetched = True
                    stable_rows = await self._search_vector_only(
                        search_text=search_text,
                        permalink=permalink,
                        permalink_match=permalink_match,
                        title=title,
                        note_types=note_types,
                        after_date=after_date,
                        search_item_types=search_item_types,
                        categories=categories,
                        metadata_filters=metadata_filters,
                        file_path_prefix=file_path_prefix,
                        temporal=temporal,
                        min_similarity=min_similarity,
                        limit=stable_candidate_limit,
                        offset=0,
                        candidate_limit=stable_candidate_limit,
                        _emit_observability_log=False,
                        _apply_rerank=False,
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
        params: dict[str, Any] = {
            **{f"id_{idx}": rid for idx, rid in enumerate(row_ids)},
            "project_id": self.project_id,
        }
        sql = f"""
            SELECT
                project_id, id, title, permalink, file_path, type, metadata,
                from_id, to_id, relation_type, entity_id, content_snippet,
                category, created_at, updated_at, 0 as score
            FROM search_index
            WHERE project_id = :project_id
              AND id IN ({placeholders})
        """
        result: dict[SearchIndexKey, SearchIndexRow] = {}
        async with db.scoped_session(self.session_maker) as session:
            row_result = await session.execute(text(sql), params)
            for row in row_result.fetchall():
                search_row = SearchIndexRow.from_mapping(row._asdict())
                result[(search_row.type, search_row.id)] = search_row
        return result

    # ------------------------------------------------------------------
    # Shared semantic search: hybrid score-based fusion
    # ------------------------------------------------------------------

    async def _search_hybrid(
        self,
        *,
        search_text: str,
        permalink: Optional[str],
        permalink_match: Optional[str],
        title: Optional[str],
        note_types: Optional[List[str]],
        after_date: Optional[datetime],
        search_item_types: Optional[List[SearchItemType]],
        categories: Optional[List[str]],
        metadata_filters: Optional[dict[str, Any]],
        file_path_prefix: Optional[str],
        temporal: Optional[TemporalFilter],
        min_similarity: Optional[float] = None,
        limit: int,
        offset: int,
        _candidate_limit_override: int | None = None,
        _apply_rerank: bool = True,
        _emit_observability_log: bool = True,
        trace: SearchTraceCollector | None = None,
    ) -> List[SearchIndexRow]:
        """Fuse FTS and vector results using score-based fusion.

        Uses the search_index (type, id) pair as the fusion key. The formula
        ``max(vec, fts) + FUSION_BONUS * min(vec, fts)`` preserves
        the dominant signal and rewards dual-source agreement.
        """
        self._assert_semantic_available()
        query_text = search_text.strip()
        rerank_configured = self._should_rerank(query_text)
        rerank_enabled = _apply_rerank and rerank_configured
        query_start = time.perf_counter()
        candidate_limit = (
            _candidate_limit_override
            if _candidate_limit_override is not None
            else self._candidate_limit(limit, offset, query_text)
        )
        fts_start = time.perf_counter()
        # allow_relaxed: question-form queries rarely AND-match, and a dead FTS
        # branch silently degrades hybrid to vector-only ranking. Fusion plus
        # bm25 keep relaxed lexical candidates from dominating precision.
        fts_results = await self.search(
            search_text=search_text,
            permalink=permalink,
            permalink_match=permalink_match,
            title=title,
            note_types=note_types,
            after_date=after_date,
            search_item_types=search_item_types,
            categories=categories,
            metadata_filters=metadata_filters,
            file_path_prefix=file_path_prefix,
            temporal=temporal,
            retrieval_mode=SearchRetrievalMode.FTS,
            limit=candidate_limit,
            offset=0,
            allow_relaxed=True,
            trace=trace,
        )
        fts_ms = (time.perf_counter() - fts_start) * 1000
        vector_start = time.perf_counter()
        vector_results = await self._search_vector_only(
            search_text=search_text,
            permalink=permalink,
            permalink_match=permalink_match,
            title=title,
            note_types=note_types,
            after_date=after_date,
            search_item_types=search_item_types,
            categories=categories,
            metadata_filters=metadata_filters,
            file_path_prefix=file_path_prefix,
            temporal=temporal,
            min_similarity=min_similarity,
            limit=candidate_limit,
            offset=0,
            # Trigger: reranking owns a bounded candidate window shared by both legs.
            # Why: the disabled path historically expands the vector leg again to
            # preserve recall when many vector chunks collapse into a few search rows.
            # Outcome: avoid double expansion only when reranking is actually active.
            candidate_limit=candidate_limit if rerank_configured else None,
            _emit_observability_log=False,
            _apply_rerank=False,
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

        # --- Score-based fusion keyed on (type, id) ---
        # A bare row id collides across row types (independent id sequences), so
        # fusion must key on (type, id) or distinct rows would merge (#982).
        # FTS scores are normalized to [0, 1] (BM25 is unbounded).
        # Vector scores are used raw — already calibrated [0, 1] by _distance_to_similarity().
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
            # Trigger: FTS-only results have no matched_chunk_text from vector search.
            # Why: without chunk text, API falls back to truncated content, losing answer text.
            # Outcome: FTS-only results get full content_snippet as matched_chunk.
            if row.matched_chunk_text is None and row.content_snippet:
                row = replace(row, matched_chunk_text=row.content_snippet)
            return replace(row, score=fused_score)

        # Rerank the top fused candidates before paginating. When reranking is active
        # we materialize the whole candidate list (cheap next to a cross-encoder call)
        # and hand it to the shared paginate helper; the disabled path stays cheap by
        # materializing only the requested page.
        if rerank_enabled:
            candidates = [_materialize(entry) for entry in ranked]
            stable_candidates = candidates
            stable_candidate_limit = self._rerank_candidate_limit()
            if candidate_limit > stable_candidate_limit:
                if trace is not None:
                    trace.stable_pool_refetched = True
                stable_candidates = await self._search_hybrid(
                    search_text=search_text,
                    permalink=permalink,
                    permalink_match=permalink_match,
                    title=title,
                    note_types=note_types,
                    after_date=after_date,
                    search_item_types=search_item_types,
                    categories=categories,
                    metadata_filters=metadata_filters,
                    file_path_prefix=file_path_prefix,
                    temporal=temporal,
                    min_similarity=min_similarity,
                    limit=stable_candidate_limit,
                    offset=0,
                    _candidate_limit_override=stable_candidate_limit,
                    _apply_rerank=False,
                    _emit_observability_log=False,
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
        if _emit_observability_log and total_ms > 2500:
            logger.warning(
                "[SEMANTIC_SLOW_QUERY] Semantic query timing: project_id={project_id} "
                "retrieval_mode={retrieval_mode} query_length={query_length} "
                "candidate_limit={candidate_limit} fts_count={fts_count} "
                "vector_count={vector_count} fts_ms={fts_ms:.2f} vector_ms={vector_ms:.2f} "
                "fusion_ms={fusion_ms:.2f} total_ms={total_ms:.2f}",
                project_id=self.project_id,
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
