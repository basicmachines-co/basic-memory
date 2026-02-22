"""Abstract base class for search repository implementations."""

import hashlib
import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import Executable, Result, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.semantic_errors import (
    SemanticDependenciesMissingError,
    SemanticSearchDisabledError,
)
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode

# --- Semantic search constants ---

VECTOR_FILTER_SCAN_LIMIT = 50000
RRF_K = 60
MAX_VECTOR_CHUNK_CHARS = 900
VECTOR_CHUNK_OVERLAP_CHARS = 120
HEADER_LINE_PATTERN = re.compile(r"^\s*#{1,6}\s+")
BULLET_PATTERN = re.compile(r"^[\-\*]\s+")


class SearchRepositoryBase(ABC):
    """Abstract base class for backend-specific search repository implementations.

    This class defines the common interface that all search repositories must implement,
    regardless of whether they use SQLite FTS5 or Postgres tsvector for full-text search.

    Shared semantic search logic (chunking, embedding orchestration, hybrid RRF fusion)
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
        metadata_filters: Optional[Dict[str, Any]] = None,
        retrieval_mode: SearchRetrievalMode = SearchRetrievalMode.FTS,
        min_similarity: Optional[float] = None,
        limit: int = 10,
        offset: int = 0,
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
    async def _update_timestamp_sql(self) -> str:
        """Return the SQL expression for current timestamp in the backend."""
        pass  # pragma: no cover

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

    def _compose_row_source_text(self, row) -> str:
        """Build the text blob that will be chunked and embedded for one search_index row.

        For entity rows we use title, permalink, and content_snippet (the actual
        human-readable content).  content_stems is an FTS-optimised variant that
        includes word-boundary expansions and would dilute embedding quality.
        """
        if row.type == SearchItemType.ENTITY.value:
            row_parts = [
                row.title or "",
                row.permalink or "",
                row.content_snippet or "",
            ]
            return "\n\n".join(part for part in row_parts if part)

        if row.type == SearchItemType.OBSERVATION.value:
            row_parts = [
                row.title or "",
                row.permalink or "",
                row.category or "",
                row.content_snippet or "",
            ]
            return "\n\n".join(part for part in row_parts if part)

        row_parts = [
            row.title or "",
            row.permalink or "",
            row.relation_type or "",
            row.content_snippet or "",
        ]
        return "\n\n".join(part for part in row_parts if part)

    def _build_chunk_records(self, rows) -> list[dict[str, str]]:
        records: list[dict[str, str]] = []
        for row in rows:
            source_text = self._compose_row_source_text(row)
            chunks = self._split_text_into_chunks(source_text)
            for chunk_index, chunk_text in enumerate(chunks):
                chunk_key = f"{row.type}:{row.id}:{chunk_index}"
                source_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
                records.append(
                    {
                        "chunk_key": chunk_key,
                        "chunk_text": chunk_text,
                        "source_hash": source_hash,
                    }
                )
        return records

    # --- Text splitting ---

    def _split_text_into_chunks(self, text_value: str) -> list[str]:
        normalized = (text_value or "").strip()
        if not normalized:
            return []

        # Split on markdown headers AND bullet boundaries to ensure each
        # discrete fact gets its own embedding vector for granular retrieval.
        lines = normalized.splitlines()
        sections: list[str] = []
        current_section: list[str] = []
        for line in lines:
            if HEADER_LINE_PATTERN.match(line) and current_section:
                sections.append("\n".join(current_section).strip())
                current_section = [line]
            elif BULLET_PATTERN.match(line) and current_section:
                sections.append("\n".join(current_section).strip())
                current_section = [line]
            else:
                current_section.append(line)
        if current_section:
            sections.append("\n".join(current_section).strip())

        chunked_sections: list[str] = []
        current_chunk = ""

        for section in sections:
            is_bullet = bool(BULLET_PATTERN.match(section))

            if len(section) > MAX_VECTOR_CHUNK_CHARS:
                if current_chunk:
                    chunked_sections.append(current_chunk)
                    current_chunk = ""
                long_chunks = self._split_long_section(section)
                if long_chunks:
                    chunked_sections.extend(long_chunks[:-1])
                    current_chunk = long_chunks[-1]
                continue

            # Keep bullets as individual chunks for granular fact retrieval.
            # Non-bullet sections (headers, prose) merge up to MAX_VECTOR_CHUNK_CHARS.
            if is_bullet:
                if current_chunk:
                    chunked_sections.append(current_chunk)
                    current_chunk = ""
                chunked_sections.append(section)
                continue

            candidate = section if not current_chunk else f"{current_chunk}\n\n{section}"
            if len(candidate) <= MAX_VECTOR_CHUNK_CHARS:
                current_chunk = candidate
                continue

            chunked_sections.append(current_chunk)
            current_chunk = section

        if current_chunk:
            chunked_sections.append(current_chunk)

        return [chunk for chunk in chunked_sections if chunk.strip()]

    @staticmethod
    def _split_into_paragraphs(section_text: str) -> list[str]:
        """Split section into paragraphs, treating bullet lists as separate items.

        Double newlines always split. Within a single-newline block, bullet
        boundaries (lines starting with - or *) also create splits so that
        individual facts in a list become separate embeddable chunks.
        """
        raw_paragraphs = [p.strip() for p in section_text.split("\n\n") if p.strip()]
        result: list[str] = []
        for para in raw_paragraphs:
            lines = para.split("\n")
            # Check if this paragraph contains bullet items
            has_bullets = any(BULLET_PATTERN.match(line) for line in lines)
            if not has_bullets:
                result.append(para)
                continue
            # Split on bullet boundaries: group consecutive non-bullet lines
            # with their preceding bullet
            current_item: list[str] = []
            for line in lines:
                if BULLET_PATTERN.match(line) and current_item:
                    result.append("\n".join(current_item).strip())
                    current_item = [line]
                else:
                    current_item.append(line)
            if current_item:
                result.append("\n".join(current_item).strip())
        return [p for p in result if p]

    def _split_long_section(self, section_text: str) -> list[str]:
        paragraphs = self._split_into_paragraphs(section_text)
        if not paragraphs:
            return []

        chunks: list[str] = []
        current = ""
        for paragraph in paragraphs:
            if len(paragraph) > MAX_VECTOR_CHUNK_CHARS:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(self._split_by_char_window(paragraph))
                continue

            candidate = paragraph if not current else f"{current}\n\n{paragraph}"
            if len(candidate) <= MAX_VECTOR_CHUNK_CHARS:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = paragraph

        if current:
            chunks.append(current)
        return chunks

    def _split_by_char_window(self, paragraph: str) -> list[str]:
        text_value = paragraph.strip()
        if not text_value:
            return []

        chunks: list[str] = []
        start = 0
        while start < len(text_value):
            end = min(len(text_value), start + MAX_VECTOR_CHUNK_CHARS)
            chunk = text_value[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(text_value):
                break
            start = max(0, end - VECTOR_CHUNK_OVERLAP_CHARS)
        return chunks

    # ------------------------------------------------------------------
    # Shared semantic search: sync_entity_vectors orchestration
    # ------------------------------------------------------------------

    async def sync_entity_vectors(self, entity_id: int) -> None:
        """Sync semantic chunk rows + embeddings for a single entity.

        This is the shared orchestration logic. Backend-specific SQL operations
        are delegated to abstract hooks (_delete_entity_chunks, _write_embeddings, etc.).
        """
        self._assert_semantic_available()
        await self._ensure_vector_tables()
        assert self._embedding_provider is not None

        async with db.scoped_session(self.session_maker) as session:
            await self._prepare_vector_session(session)

            row_result = await session.execute(
                text(
                    "SELECT id, type, title, permalink, content_stems, content_snippet, "
                    "category, relation_type "
                    "FROM search_index "
                    "WHERE entity_id = :entity_id AND project_id = :project_id "
                    "ORDER BY "
                    "CASE type "
                    "WHEN :entity_type THEN 0 "
                    "WHEN :observation_type THEN 1 "
                    "WHEN :relation_type_type THEN 2 "
                    "ELSE 3 END, id ASC"
                ),
                {
                    "entity_id": entity_id,
                    "project_id": self.project_id,
                    "entity_type": SearchItemType.ENTITY.value,
                    "observation_type": SearchItemType.OBSERVATION.value,
                    "relation_type_type": SearchItemType.RELATION.value,
                },
            )
            rows = row_result.fetchall()

            # No search_index rows → delete all chunk/embedding data for this entity.
            if not rows:
                await self._delete_entity_chunks(session, entity_id)
                await session.commit()
                return

            chunk_records = self._build_chunk_records(rows)
            if not chunk_records:
                await self._delete_entity_chunks(session, entity_id)
                await session.commit()
                return

            # --- Diff existing chunks against incoming ---
            existing_rows_result = await session.execute(
                text(
                    "SELECT id, chunk_key, source_hash "
                    "FROM search_vector_chunks "
                    "WHERE project_id = :project_id AND entity_id = :entity_id"
                ),
                {"project_id": self.project_id, "entity_id": entity_id},
            )
            existing_by_key = {row.chunk_key: row for row in existing_rows_result.fetchall()}
            incoming_hashes = {
                record["chunk_key"]: record["source_hash"] for record in chunk_records
            }
            stale_ids = [
                int(row.id)
                for chunk_key, row in existing_by_key.items()
                if chunk_key not in incoming_hashes
            ]

            if stale_ids:
                await self._delete_stale_chunks(session, stale_ids, entity_id)

            # --- Orphan cleanup: chunks without corresponding embeddings ---
            # Trigger: a previous sync crashed between chunk insert and embedding write.
            # Why: self-healing on next sync prevents permanent data skew.
            # Outcome: orphaned chunks are re-embedded instead of silently dropped.
            orphan_result = await session.execute(
                text(self._orphan_detection_sql()),
                {"project_id": self.project_id, "entity_id": entity_id},
            )
            orphan_rows = orphan_result.fetchall()

            # --- Upsert changed / new chunks, collect embedding jobs ---
            timestamp_expr = self._timestamp_now_expr()
            embedding_jobs: list[tuple[int, str]] = []
            for record in chunk_records:
                current = existing_by_key.get(record["chunk_key"])

                # Trigger: chunk exists and hash matches (no content change)
                # but chunk has no embedding (orphan from crash).
                # Outcome: schedule re-embedding without touching chunk metadata.
                is_orphan = current and any(o.id == current.id for o in orphan_rows)
                if current and current.source_hash == record["source_hash"] and not is_orphan:
                    continue

                if current:
                    row_id = int(current.id)
                    if current.source_hash != record["source_hash"]:
                        await session.execute(
                            text(
                                "UPDATE search_vector_chunks "
                                "SET chunk_text = :chunk_text, source_hash = :source_hash, "
                                f"updated_at = {timestamp_expr} "
                                "WHERE id = :id"
                            ),
                            {
                                "id": row_id,
                                "chunk_text": record["chunk_text"],
                                "source_hash": record["source_hash"],
                            },
                        )
                    embedding_jobs.append((row_id, record["chunk_text"]))
                    continue

                inserted = await session.execute(
                    text(
                        "INSERT INTO search_vector_chunks ("
                        "entity_id, project_id, chunk_key, chunk_text, source_hash, updated_at"
                        ") VALUES ("
                        f":entity_id, :project_id, :chunk_key, :chunk_text, :source_hash, "
                        f"{timestamp_expr}"
                        ") RETURNING id"
                    ),
                    {
                        "entity_id": entity_id,
                        "project_id": self.project_id,
                        "chunk_key": record["chunk_key"],
                        "chunk_text": record["chunk_text"],
                        "source_hash": record["source_hash"],
                    },
                )
                row_id = int(inserted.scalar_one())
                embedding_jobs.append((row_id, record["chunk_text"]))

            await session.commit()

        if not embedding_jobs:
            return

        texts = [t for _, t in embedding_jobs]
        embeddings = await self._embedding_provider.embed_documents(texts)
        if len(embeddings) != len(embedding_jobs):
            raise RuntimeError("Embedding provider returned an unexpected number of vectors.")

        async with db.scoped_session(self.session_maker) as session:
            await self._prepare_vector_session(session)
            await self._write_embeddings(session, embedding_jobs, embeddings)
            await session.commit()

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

    def _orphan_detection_sql(self) -> str:
        """SQL to find chunk rows without corresponding embeddings.

        Default implementation works for both backends; SQLite overrides
        to reference the rowid-based embedding table layout.
        """
        return (
            "SELECT c.id FROM search_vector_chunks c "
            "LEFT JOIN search_vector_embeddings e ON e.chunk_id = c.id "
            "WHERE c.project_id = :project_id AND c.entity_id = :entity_id "
            "AND e.chunk_id IS NULL"
        )

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
    def _parse_chunk_key(chunk_key: str) -> tuple[str, int]:
        """Parse a chunk_key like 'observation:5:0' into (type, search_index_id)."""
        parts = chunk_key.split(":")
        return parts[0], int(parts[1])

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
        metadata_filters: Optional[dict],
        min_similarity: Optional[float] = None,
        limit: int,
        offset: int,
    ) -> List[SearchIndexRow]:
        """Run vector-only search returning chunk-level results.

        Returns individual search_index rows (entities, observations, relations)
        ranked by vector similarity. Each observation or relation is a first-class
        result, not collapsed into its parent entity.
        """
        self._assert_semantic_available()
        await self._ensure_vector_tables()
        assert self._embedding_provider is not None
        query_embedding = await self._embedding_provider.embed_query(search_text.strip())
        candidate_limit = max(self._semantic_vector_k, (limit + offset) * 10)

        async with db.scoped_session(self.session_maker) as session:
            await self._prepare_vector_session(session)
            vector_rows = await self._run_vector_query(session, query_embedding, candidate_limit)

        if not vector_rows:
            return []

        # Build per-search_index_row similarity scores from chunk-level results.
        # Each chunk_key encodes the search_index row type and id.
        # Keep the best similarity per search_index row id.
        similarity_by_si_id: dict[int, float] = {}
        for row in vector_rows:
            chunk_key = row.get("chunk_key", "")
            distance = float(row["best_distance"])
            similarity = self._distance_to_similarity(distance)
            try:
                _, si_id = self._parse_chunk_key(chunk_key)
            except (ValueError, IndexError):
                # Fallback: group by entity_id for chunks without parseable keys
                continue
            current = similarity_by_si_id.get(si_id)
            if current is None or similarity > current:
                similarity_by_si_id[si_id] = similarity

        if not similarity_by_si_id:
            return []

        # Filter out results below the minimum similarity threshold.
        # Per-query min_similarity overrides the instance-level default.
        effective_min_similarity = (
            min_similarity if min_similarity is not None else self._semantic_min_similarity
        )
        if effective_min_similarity > 0.0:
            similarity_by_si_id = {
                k: v for k, v in similarity_by_si_id.items() if v >= effective_min_similarity
            }
            if not similarity_by_si_id:
                return []

        # Fetch the actual search_index rows
        si_ids = list(similarity_by_si_id.keys())
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
                metadata_filters=metadata_filters,
                retrieval_mode=SearchRetrievalMode.FTS,
                limit=VECTOR_FILTER_SCAN_LIMIT,
                offset=0,
            )
            # Use (id, type) tuples to avoid collisions between different
            # search_index row types that share the same auto-increment id.
            allowed_keys = {(row.id, row.type) for row in filtered_rows if row.id is not None}
            search_index_rows = {
                k: v for k, v in search_index_rows.items() if (v.id, v.type) in allowed_keys
            }

        ranked_rows: list[SearchIndexRow] = []
        for si_id, similarity in similarity_by_si_id.items():
            row = search_index_rows.get(si_id)
            if row is None:
                continue
            ranked_rows.append(replace(row, score=similarity))

        ranked_rows.sort(key=lambda item: item.score or 0.0, reverse=True)
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
                result[row.entity_id] = SearchIndexRow(
                    project_id=self.project_id,
                    id=row.id,
                    title=row.title,
                    permalink=row.permalink,
                    file_path=row.file_path,
                    type=row.type,
                    score=0.0,
                    metadata=(
                        row.metadata
                        if isinstance(row.metadata, dict)
                        else (json.loads(row.metadata) if row.metadata else {})
                    ),
                    from_id=row.from_id,
                    to_id=row.to_id,
                    relation_type=row.relation_type,
                    entity_id=row.entity_id,
                    content_snippet=row.content_snippet,
                    category=row.category,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
        return result

    async def _fetch_search_index_rows_by_ids(
        self, row_ids: list[int]
    ) -> dict[int, SearchIndexRow]:
        """Fetch search_index rows by their primary key (id), any type."""
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
        result: dict[int, SearchIndexRow] = {}
        async with db.scoped_session(self.session_maker) as session:
            row_result = await session.execute(text(sql), params)
            for row in row_result.fetchall():
                result[row.id] = SearchIndexRow(
                    project_id=self.project_id,
                    id=row.id,
                    title=row.title,
                    permalink=row.permalink,
                    file_path=row.file_path,
                    type=row.type,
                    score=0.0,
                    metadata=(
                        row.metadata
                        if isinstance(row.metadata, dict)
                        else (json.loads(row.metadata) if row.metadata else {})
                    ),
                    from_id=row.from_id,
                    to_id=row.to_id,
                    relation_type=row.relation_type,
                    entity_id=row.entity_id,
                    content_snippet=row.content_snippet,
                    category=row.category,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
        return result

    # ------------------------------------------------------------------
    # Shared semantic search: hybrid RRF fusion
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
        metadata_filters: Optional[dict],
        min_similarity: Optional[float] = None,
        limit: int,
        offset: int,
    ) -> List[SearchIndexRow]:
        """Fuse FTS and vector rankings using reciprocal rank fusion (RRF).

        Uses entity_id as the fusion key (not permalink) to correctly handle
        entities with NULL permalinks.
        """
        self._assert_semantic_available()
        candidate_limit = max(self._semantic_vector_k, (limit + offset) * 10)
        fts_results = await self.search(
            search_text=search_text,
            permalink=permalink,
            permalink_match=permalink_match,
            title=title,
            note_types=note_types,
            after_date=after_date,
            search_item_types=search_item_types,
            metadata_filters=metadata_filters,
            retrieval_mode=SearchRetrievalMode.FTS,
            limit=candidate_limit,
            offset=0,
        )
        vector_results = await self._search_vector_only(
            search_text=search_text,
            permalink=permalink,
            permalink_match=permalink_match,
            title=title,
            note_types=note_types,
            after_date=after_date,
            search_item_types=search_item_types,
            metadata_filters=metadata_filters,
            min_similarity=min_similarity,
            limit=candidate_limit,
            offset=0,
        )

        # Score-weighted RRF fusion keyed on search_index row id.
        # Multiplies the standard 1/(k+rank) score by the normalized original score
        # so that high-confidence matches contribute more than weak ones at the same rank.
        fused_scores: dict[int, float] = {}
        rows_by_id: dict[int, SearchIndexRow] = {}

        # Normalize FTS scores to [0, 1] — handles both SQLite (negative bm25)
        # and Postgres (positive ts_rank) by using absolute values
        fts_abs = [abs(row.score or 0.0) for row in fts_results]
        fts_max = max(fts_abs) if fts_abs else 1.0

        for rank, row in enumerate(fts_results, start=1):
            if row.id is None:
                continue
            norm = abs(row.score or 0.0) / fts_max if fts_max > 0 else 0.0
            weight = max(norm, 0.1)  # floor preserves RRF stability
            fused_scores[row.id] = fused_scores.get(row.id, 0.0) + weight * (1.0 / (RRF_K + rank))
            rows_by_id[row.id] = row

        # Vector scores already in [0, 1] from the similarity formula
        vec_max = max((row.score or 0.0) for row in vector_results) if vector_results else 1.0

        for rank, row in enumerate(vector_results, start=1):
            if row.id is None:
                continue
            norm = (row.score or 0.0) / vec_max if vec_max > 0 else 0.0
            weight = max(norm, 0.1)  # floor preserves RRF stability
            fused_scores[row.id] = fused_scores.get(row.id, 0.0) + weight * (1.0 / (RRF_K + rank))
            rows_by_id[row.id] = row

        ranked = sorted(fused_scores.items(), key=lambda item: item[1], reverse=True)
        output: list[SearchIndexRow] = []
        for row_id, fused_score in ranked[offset : offset + limit]:
            output.append(replace(rows_by_id[row_id], score=fused_score))
        return output
