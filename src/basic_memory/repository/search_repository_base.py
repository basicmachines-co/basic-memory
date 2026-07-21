"""Abstract base class for search repository implementations."""

import time
from abc import ABC, abstractmethod
from collections.abc import Iterable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import logfire as logfire
from loguru import logger
from sqlalchemy import Executable, Result, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.repository import semantic_vector_sync
from basic_memory.repository.embedding_provider import (
    EmbeddingProvider,
    embedding_provider_identity,
)
from basic_memory.repository.rerank_provider import RerankProvider
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.semantic_chunking import (
    SemanticSourceRow,
    VectorChunkRecord,
    build_entity_fingerprint,
    build_vector_chunk_records,
    compose_row_source_text,
    split_text_into_chunks,
)
from basic_memory.repository.semantic_errors import (
    RerankProviderContractError,
    SemanticDependenciesMissingError,
    SemanticSearchDisabledError,
)
from basic_memory.repository.semantic_vector_sync import (
    EntitySyncRuntime as _EntitySyncRuntime,
    EntityVectorShardPlan as _EntityVectorShardPlan,
    PendingEmbeddingJob as _PendingEmbeddingJob,
    PreparedEntityVectorSync as _PreparedEntityVectorSync,
    VectorChunkState,
    VectorSyncBatchResult,
)
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode

# --- Semantic search constants ---

VECTOR_FILTER_SCAN_LIMIT = 50000
# Over-fetch factor for the rerank candidate chunk pool: chunks collapse to unique
# (type, id) rows before reranking, so fetch several times reranker_candidates chunks
# to keep enough unique documents in the rerank window.
RERANK_POOL_CHUNK_FANOUT = 4
FUSION_BONUS = 0.3
FTS_GATE_THRESHOLD = 0.0
TOP_CHUNKS_PER_RESULT = 5
SMALL_NOTE_CONTENT_LIMIT = 2000
OVERSIZED_ENTITY_VECTOR_SHARD_SIZE = semantic_vector_sync.OVERSIZED_ENTITY_VECTOR_SHARD_SIZE
_SQLITE_MAX_PREPARE_WINDOW = semantic_vector_sync.SQLITE_MAX_PREPARE_WINDOW

# Entity, observation, and relation rows in search_index carry ids from independent
# auto-increment sequences, so a bare id is ambiguous across row types. Every map in
# the vector/hybrid retrieval path must key rows by (type, id) to avoid collisions.
type SearchIndexKey = tuple[str, int]


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
        retrieval_mode: SearchRetrievalMode = SearchRetrievalMode.FTS,
        min_similarity: Optional[float] = None,
        limit: int = 10,
        offset: int = 0,
        allow_relaxed: bool = False,
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

    @abstractmethod
    async def _run_vector_query(
        self,
        session: AsyncSession,
        query_embedding: list[float],
        candidate_limit: int,
    ) -> list[dict]:
        """Execute backend-specific nearest-neighbour vector query.

        Returns list of mappings with keys ``entity_id`` and ``best_distance``.
        """
        pass

    @abstractmethod
    async def _write_embeddings(
        self,
        session: AsyncSession,
        jobs: list[tuple[int, str]],
        embeddings: list[list[float]],
    ) -> None:
        """Write embedding vectors for the given chunk row IDs.

        ``jobs`` is a list of ``(chunk_row_id, chunk_text)`` pairs.
        ``embeddings`` is the corresponding list of vectors.
        """
        pass

    @abstractmethod
    async def _delete_entity_chunks(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> None:
        """Delete all chunk + embedding rows for an entity.

        SQLite must explicitly delete embeddings first (no CASCADE).
        Postgres relies on ON DELETE CASCADE from the FK.
        """
        pass

    @abstractmethod
    async def _delete_stale_chunks(
        self,
        session: AsyncSession,
        stale_ids: list[int],
        entity_id: int,
    ) -> None:
        """Delete stale chunk rows (and their embeddings) by ID."""
        pass

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

            # Insert new record
            await session.execute(
                text("""
                    INSERT INTO search_index (
                        id, title, content_stems, content_snippet, permalink, file_path, type, metadata,
                        from_id, to_id, relation_type,
                        entity_id, category,
                        created_at, updated_at,
                        project_id
                    ) VALUES (
                        :id, :title, :content_stems, :content_snippet, :permalink, :file_path, :type, :metadata,
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
                insert_data_list.append(insert_data)

            # Batch insert all records using executemany
            await session.execute(
                text("""
                    INSERT INTO search_index (
                        id, title, content_stems, content_snippet, permalink, file_path, type, metadata,
                        from_id, to_id, relation_type,
                        entity_id, category,
                        created_at, updated_at,
                        project_id
                    ) VALUES (
                        :id, :title, :content_stems, :content_snippet, :permalink, :file_path, :type, :metadata,
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
            await self._prepare_vector_session(session)
            await self._delete_entity_chunks(session, entity_id)
            await session.commit()

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

    def _compose_row_source_text(self, row: SemanticSourceRow) -> str:
        """Build the text blob that will be chunked and embedded for one search_index row.

        For entity rows we use title, permalink, and content_snippet (the actual
        human-readable content).  content_stems is an FTS-optimised variant that
        includes word-boundary expansions and would dilute embedding quality.
        """
        return compose_row_source_text(row)

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
        return await self._sync_entity_vectors_internal(
            entity_ids,
            progress_callback=progress_callback,
            continue_on_error=True,
        )

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

    async def _prepare_entity_vector_jobs(
        self,
        entity_id: int,
    ) -> _PreparedEntityVectorSync:
        """Prepare chunk mutations and embedding jobs for one entity."""
        return await semantic_vector_sync.prepare_entity_vector_jobs(self, entity_id)

    async def _prepare_entity_vector_jobs_prefetched(
        self,
        *,
        entity_id: int,
        source_rows: list[Any],
        existing_rows: list[VectorChunkState],
    ) -> _PreparedEntityVectorSync:
        """Prepare one entity using prefetched window rows."""
        return await semantic_vector_sync.prepare_entity_vector_jobs_prefetched(
            self,
            entity_id=entity_id,
            source_rows=source_rows,
            existing_rows=existing_rows,
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
    ) -> list[tuple[int, str]]:
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
        metadata_filters: Optional[dict],
        retrieval_mode: SearchRetrievalMode,
        min_similarity: Optional[float] = None,
        limit: int,
        offset: int,
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
                min_similarity=min_similarity,
                limit=limit,
                offset=offset,
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
                min_similarity=min_similarity,
                limit=limit,
                offset=offset,
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
        candidate_limit = max(self._semantic_vector_k, (limit + offset) * 10)
        if self._should_rerank(query_text):
            candidate_limit = max(
                candidate_limit, self._reranker_candidates * RERANK_POOL_CHUNK_FANOUT
            )
        return candidate_limit

    def _rerank_document_text(self, row: SearchIndexRow) -> str:
        """Build the document text handed to the cross-encoder for one candidate.

        Prefer the matched chunk (the most relevant passage of a large note),
        falling back to the stored snippet, and prepend the title so short or
        title-only rows still carry a usable signal. Truncate to
        ``reranker_max_document_chars`` (when set) so long notes don't inflate
        cross-encoder latency — the leading text carries the strongest signal.
        """
        body = row.matched_chunk_text or row.content_snippet or ""
        title = row.title or ""
        text = f"{title}\n{body}" if (title and body) else (body or title)
        limit = self._reranker_max_document_chars
        if limit > 0 and len(text) > limit:
            text = text[:limit]
        return text

    @staticmethod
    def _demote_tail(tail: list[SearchIndexRow], floor: float) -> list[SearchIndexRow]:
        """Rescore un-reranked tail rows into ``(0, floor)`` preserving their order.

        The reranked pool carries [0, 1] relevance scores while the tail still holds
        raw retrieval scores on a different scale ([0, 1.3] for fused hybrid). Left as
        is, a tail row could outrank a reranked row numerically — corrupting the public
        score column and any downstream re-sort (e.g. cross-project merge). Pinning the
        tail strictly below the reranked floor keeps the whole page monotonic and in
        [0, 1] without a second provider call.
        """
        count = len(tail)
        return [
            replace(row, score=floor * (count - index) / (count + 1))
            for index, row in enumerate(tail)
        ]

    async def _rerank_and_paginate(
        self,
        query_text: str,
        rows: list[SearchIndexRow],
        *,
        offset: int,
        limit: int,
    ) -> list[SearchIndexRow]:
        """Rerank the top candidates, then return the requested ``[offset:offset+limit]`` page.

        Trigger: a reranker is configured and there is a real query.
        Why: bi-encoder/FTS ranking lands the gold document in the top-N but often
        just below the top-k cutoff (#950); a cross-encoder that reads query and
        document together recovers those near-misses.
        Outcome: the first ``reranker_candidates`` rows are reordered by reranker
        relevance (which replaces ``score``); the requested page is sliced from the
        reordered list.
        """
        page_end = offset + limit
        # Skip the provider entirely when reranking cannot change the requested page:
        # no reranker, no query, too few rows, or the whole page sits past the rerank
        # pool (a deep page — reranking it would rescore rows we never return, and for
        # a paid API that is wasted spend).
        if (
            self._rerank_provider is None
            or not query_text
            or len(rows) < 2
            or offset >= self._reranker_candidates
        ):
            return rows[offset:page_end]

        pool = rows[: self._reranker_candidates]
        tail = rows[self._reranker_candidates :]
        documents = [self._rerank_document_text(row) for row in pool]
        try:
            scores = await self._rerank_provider.rerank(query_text, documents)
        except (SemanticDependenciesMissingError, RerankProviderContractError):
            # Permanent faults — missing reranker deps, or a provider that broke its
            # response contract (e.g. an incomplete rerank response). Surface these like
            # the embedding path does; masking them would leave a permanently broken
            # reranker silently returning un-reranked results with no signal.
            raise
        except Exception as exc:
            # Transient faults — timeout, connection reset, provider outage. Reranking is
            # an opt-in enhancement, so a blip must not turn good FTS/vector results into a
            # hard search outage; degrade to retrieval order for this query.
            logger.warning(
                "Reranker failed; returning retrieval order. model={model} error={error}",
                model=self._rerank_provider.model_name,
                error=exc,
            )
            return rows[offset:page_end]
        # Fail fast on a length mismatch: that is a provider bug, not a transient fault,
        # and must not be papered over by truncating or zero-filling.
        if len(scores) != len(pool):
            raise RerankProviderContractError(
                f"Reranker returned {len(scores)} scores for {len(pool)} documents."
            )

        order = sorted(range(len(pool)), key=lambda i: scores[i], reverse=True)
        reranked = [replace(pool[i], score=scores[i]) for i in order]
        logger.debug(
            "Reranked candidates: pool={pool} model={model}",
            pool=len(pool),
            model=self._rerank_provider.model_name,
        )
        demoted_tail = self._demote_tail(tail, floor=reranked[-1].score or 0.0)
        return (reranked + demoted_tail)[offset:page_end]

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
        metadata_filters: Optional[dict],
        min_similarity: Optional[float] = None,
        limit: int,
        offset: int,
        _emit_observability_log: bool = True,
        _apply_rerank: bool = True,
    ) -> List[SearchIndexRow]:
        """Run vector-only search returning chunk-level results.

        Returns individual search_index rows (entities, observations, relations)
        ranked by vector similarity. Each observation or relation is a first-class
        result, not collapsed into its parent entity.
        """
        self._assert_semantic_available()
        await self._ensure_vector_tables()
        assert self._embedding_provider is not None
        query_text = search_text.strip()
        candidate_limit = self._candidate_limit(limit, offset, query_text)
        query_start = time.perf_counter()
        embed_start = time.perf_counter()
        query_embedding = await self._embedding_provider.embed_query(query_text)
        embed_ms = (time.perf_counter() - embed_start) * 1000
        vector_query_start = time.perf_counter()

        async with db.scoped_session(self.session_maker) as session:
            await self._prepare_vector_session(session)
            vector_rows = await self._run_vector_query(session, query_embedding, candidate_limit)
        vector_query_ms = (time.perf_counter() - vector_query_start) * 1000
        vector_row_count = len(vector_rows)
        hydrate_ms = 0.0

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
                retrieval_mode=SearchRetrievalMode.FTS,
                limit=VECTOR_FILTER_SCAN_LIMIT,
                offset=0,
            )
            # Use (type, id) tuples to avoid collisions between different
            # search_index row types that share the same auto-increment id.
            allowed_keys = {(row.type, row.id) for row in filtered_rows if row.id is not None}
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
        _log_vector_summary()
        # Rerank over the wide candidate pool, then slice to the page. Suppressed when
        # hybrid calls this internally (_apply_rerank=False) — hybrid reranks its own
        # fused result; _rerank_and_paginate no-ops back to a plain slice otherwise.
        if _apply_rerank:
            return await self._rerank_and_paginate(
                query_text, ranked_rows, offset=offset, limit=limit
            )
        return ranked_rows[offset : offset + limit]

    async def _fetch_entity_rows_by_ids(self, entity_ids: list[int]) -> dict[int, SearchIndexRow]:
        """Fetch entity-type search_index rows by their entity_id values."""
        placeholders = ",".join(f":id_{idx}" for idx in range(len(entity_ids)))
        params: dict[str, Any] = {
            **{f"id_{idx}": eid for idx, eid in enumerate(entity_ids)},
            "project_id": self.project_id,
            "item_type": SearchItemType.ENTITY.value,
        }
        sql = f"""
            SELECT
                project_id, id, title, permalink, file_path, type, metadata,
                from_id, to_id, relation_type, entity_id, content_snippet,
                category, created_at, updated_at, 0 as score
            FROM search_index
            WHERE project_id = :project_id
              AND type = :item_type
              AND entity_id IN ({placeholders})
        """
        result: dict[int, SearchIndexRow] = {}
        async with db.scoped_session(self.session_maker) as session:
            row_result = await session.execute(text(sql), params)
            for row in row_result.fetchall():
                search_row = SearchIndexRow.from_mapping(row._asdict())
                result[row.entity_id] = search_row
        return result

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
        metadata_filters: Optional[dict],
        min_similarity: Optional[float] = None,
        limit: int,
        offset: int,
    ) -> List[SearchIndexRow]:
        """Fuse FTS and vector results using score-based fusion.

        Uses the search_index (type, id) pair as the fusion key. The formula
        ``max(vec, fts) + FUSION_BONUS * min(vec, fts)`` preserves
        the dominant signal and rewards dual-source agreement.
        """
        self._assert_semantic_available()
        query_text = search_text.strip()
        query_start = time.perf_counter()
        candidate_limit = self._candidate_limit(limit, offset, query_text)
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
            retrieval_mode=SearchRetrievalMode.FTS,
            limit=candidate_limit,
            offset=0,
            allow_relaxed=True,
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
            min_similarity=min_similarity,
            limit=candidate_limit,
            offset=0,
            _emit_observability_log=False,
            _apply_rerank=False,
        )
        vector_ms = (time.perf_counter() - vector_start) * 1000
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
        for row in fts_results:
            if row.id is None:
                continue
            norm = abs(row.score or 0.0) / fts_max if fts_max > 0 else 0.0
            # Gate: FTS scores below threshold contribute zero
            if norm < FTS_GATE_THRESHOLD:
                norm = 0.0
            fts_scores[(row.type, row.id)] = norm
            rows_by_key[(row.type, row.id)] = row

        vec_scores: dict[SearchIndexKey, float] = {}
        for row in vector_results:
            if row.id is None:
                continue
            # Trigger: no re-normalization by vec_max
            # Why: vector similarity is already calibrated [0, 1]; re-normalizing
            # inflates weak matches when the entire result set is mediocre
            vec_scores[(row.type, row.id)] = row.score or 0.0
            rows_by_key[(row.type, row.id)] = row

        # Fuse: max(v, f) + FUSION_BONUS * min(v, f)
        # Preserves the dominant signal; bonus rewards dual-source agreement.
        # Output range: [0, 1.3] for dual-source, [0, 1.0] for single-source.
        fused_scores: dict[SearchIndexKey, float] = {}
        for row_key in fts_scores.keys() | vec_scores.keys():
            v = vec_scores.get(row_key, 0.0)
            f = fts_scores.get(row_key, 0.0)
            fused_scores[row_key] = max(v, f) + FUSION_BONUS * min(v, f)

        ranked = sorted(fused_scores.items(), key=lambda item: item[1], reverse=True)

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
        if self._should_rerank(query_text):
            candidates = [_materialize(entry) for entry in ranked]
            output = await self._rerank_and_paginate(
                query_text, candidates, offset=offset, limit=limit
            )
        else:
            output = [_materialize(entry) for entry in ranked[offset : offset + limit]]
        fusion_ms = (time.perf_counter() - fusion_start) * 1000
        total_ms = (time.perf_counter() - query_start) * 1000
        if total_ms > 2500:
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
