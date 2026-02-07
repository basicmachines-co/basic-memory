"""PostgreSQL tsvector-based search repository implementation."""

import asyncio
import hashlib
import json
import re
from dataclasses import replace
from datetime import datetime
from typing import List, Optional


from loguru import logger
from sqlalchemy import text

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.fastembed_provider import FastEmbedEmbeddingProvider
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_repository_base import SearchRepositoryBase
from basic_memory.repository.metadata_filters import (
    parse_metadata_filters,
    build_postgres_json_path,
)
from basic_memory.repository.semantic_errors import (
    SemanticDependenciesMissingError,
    SemanticSearchDisabledError,
)
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode


VECTOR_FILTER_SCAN_LIMIT = 50000
RRF_K = 60
MAX_VECTOR_CHUNK_CHARS = 900
VECTOR_CHUNK_OVERLAP_CHARS = 120
HEADER_LINE_PATTERN = re.compile(r"^\s*#{1,6}\s+")


class PostgresSearchRepository(SearchRepositoryBase):
    """PostgreSQL tsvector implementation of search repository.

    Uses PostgreSQL's full-text search capabilities with:
    - tsvector for document representation
    - tsquery for query representation
    - GIN indexes for performance
    - ts_rank() function for relevance scoring
    - JSONB containment operators for metadata search

    Note: This implementation uses UPSERT patterns (INSERT ... ON CONFLICT) instead of
    delete-then-insert to handle race conditions during parallel entity indexing.
    The partial unique index uix_search_index_permalink_project prevents duplicate
    permalinks per project.
    """

    def __init__(
        self,
        session_maker,
        project_id: int,
        app_config: BasicMemoryConfig | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ):
        super().__init__(session_maker, project_id)
        self._app_config = app_config or ConfigManager().config
        self._semantic_enabled = self._app_config.semantic_search_enabled
        self._semantic_vector_k = self._app_config.semantic_vector_k
        self._embedding_provider = embedding_provider
        self._vector_dimensions = 384
        self._vector_tables_initialized = False
        self._vector_tables_lock = asyncio.Lock()

        if self._semantic_enabled and self._embedding_provider is None:
            provider_name = self._app_config.semantic_embedding_provider.strip().lower()
            if provider_name != "fastembed":
                raise ValueError(f"Unsupported semantic embedding provider: {provider_name}")
            self._embedding_provider = FastEmbedEmbeddingProvider(
                model_name=self._app_config.semantic_embedding_model,
                batch_size=self._app_config.semantic_embedding_batch_size,
            )
        if self._embedding_provider is not None:
            self._vector_dimensions = self._embedding_provider.dimensions

    async def init_search_index(self):
        """Create Postgres table with tsvector column and GIN indexes.

        Note: This is handled by Alembic migrations. This method is a no-op
        for Postgres as the schema is created via migrations.
        """
        logger.info("PostgreSQL search index initialization handled by migrations")
        # Table creation is done via Alembic migrations
        # This includes:
        # - CREATE TABLE search_index (...)
        # - ADD COLUMN textsearchable_index_col tsvector GENERATED ALWAYS AS (...)
        # - CREATE INDEX USING GIN on textsearchable_index_col
        # - CREATE INDEX USING GIN on metadata jsonb_path_ops
        pass

    async def index_item(self, search_index_row: SearchIndexRow) -> None:
        """Index or update a single item using UPSERT.

        Uses INSERT ... ON CONFLICT to handle race conditions during parallel
        entity indexing. The partial unique index uix_search_index_permalink_project
        on (permalink, project_id) WHERE permalink IS NOT NULL prevents duplicate
        permalinks.

        For rows with non-null permalinks (entities), conflicts are resolved by
        updating the existing row. For rows with null permalinks, no conflict
        occurs on this index.
        """
        async with db.scoped_session(self.session_maker) as session:
            # Serialize JSON for raw SQL
            insert_data = search_index_row.to_insert(serialize_json=True)
            insert_data["project_id"] = self.project_id

            # Use upsert to handle race conditions during parallel indexing
            # ON CONFLICT (permalink, project_id) matches the partial unique index
            # uix_search_index_permalink_project WHERE permalink IS NOT NULL
            # For rows with NULL permalinks, no conflict occurs (partial index doesn't apply)
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
                    ON CONFLICT (permalink, project_id) WHERE permalink IS NOT NULL DO UPDATE SET
                        id = EXCLUDED.id,
                        title = EXCLUDED.title,
                        content_stems = EXCLUDED.content_stems,
                        content_snippet = EXCLUDED.content_snippet,
                        file_path = EXCLUDED.file_path,
                        type = EXCLUDED.type,
                        metadata = EXCLUDED.metadata,
                        from_id = EXCLUDED.from_id,
                        to_id = EXCLUDED.to_id,
                        relation_type = EXCLUDED.relation_type,
                        entity_id = EXCLUDED.entity_id,
                        category = EXCLUDED.category,
                        created_at = EXCLUDED.created_at,
                        updated_at = EXCLUDED.updated_at
                """),
                insert_data,
            )
            logger.debug(f"indexed row {search_index_row}")
            await session.commit()

    def _prepare_search_term(self, term: str, is_prefix: bool = True) -> str:
        """Prepare a search term for tsquery format.

        Args:
            term: The search term to prepare
            is_prefix: Whether to add prefix search capability (:* operator)

        Returns:
            Formatted search term for tsquery

        For Postgres:
        - Boolean operators are converted to tsquery format (&, |, !)
        - Prefix matching uses the :* operator
        - Terms are sanitized to prevent tsquery syntax errors
        """
        # Check for explicit boolean operators
        boolean_operators = [" AND ", " OR ", " NOT "]
        if any(op in f" {term} " for op in boolean_operators):
            return self._prepare_boolean_query(term)

        # For non-Boolean queries, prepare single term
        return self._prepare_single_term(term, is_prefix)

    def _prepare_boolean_query(self, query: str) -> str:
        """Convert Boolean query to tsquery format.

        Args:
            query: A Boolean query like "coffee AND brewing" or "(pour OR french) AND press"

        Returns:
            tsquery-formatted string with & (AND), | (OR), ! (NOT) operators

        Examples:
            "coffee AND brewing" -> "coffee & brewing"
            "(pour OR french) AND press" -> "(pour | french) & press"
            "coffee NOT decaf" -> "coffee & !decaf"
        """
        # Replace Boolean operators with tsquery operators
        # Keep parentheses for grouping
        result = query
        result = re.sub(r"\bAND\b", "&", result)
        result = re.sub(r"\bOR\b", "|", result)
        # NOT must be converted to "& !" and the ! must be attached to the following term
        # "Python NOT Django" -> "Python & !Django"
        result = re.sub(r"\bNOT\s+", "& !", result)

        return result

    def _prepare_single_term(self, term: str, is_prefix: bool = True) -> str:
        """Prepare a single search term for tsquery.

        Args:
            term: A single search term
            is_prefix: Whether to add prefix search capability (:* suffix)

        Returns:
            A properly formatted single term for tsquery

        For Postgres tsquery:
        - Multi-word queries become "word1 & word2"
        - Prefix matching uses ":*" suffix (e.g., "coff:*")
        - Special characters that need escaping: & | ! ( ) :
        """
        if not term or not term.strip():
            return term

        term = term.strip()

        # Check if term is already a wildcard pattern
        if "*" in term:
            # Replace * with :* for Postgres prefix matching
            return term.replace("*", ":*")

        # Remove tsquery special characters from the search term
        # These characters have special meaning in tsquery and cause syntax errors
        # if not used as operators
        special_chars = ["&", "|", "!", "(", ")", ":"]
        cleaned_term = term
        for char in special_chars:
            cleaned_term = cleaned_term.replace(char, " ")

        # Handle multi-word queries
        if " " in cleaned_term:
            words = [w for w in cleaned_term.split() if w.strip()]
            if not words:
                # All characters were special chars, search won't match anything
                # Return a safe search term that won't cause syntax errors
                return "NOSPECIALCHARS:*"
            if is_prefix:
                # Add prefix matching to each word
                prepared_words = [f"{word}:*" for word in words]
            else:
                prepared_words = words
            # Join with AND operator
            return " & ".join(prepared_words)

        # Single word
        cleaned_term = cleaned_term.strip()
        if is_prefix:
            return f"{cleaned_term}:*"
        else:
            return cleaned_term

    def _assert_semantic_available(self) -> None:
        if not self._semantic_enabled:
            raise SemanticSearchDisabledError(
                "Semantic search is disabled. Set BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true."
            )
        if self._embedding_provider is None:
            raise SemanticDependenciesMissingError(
                "Semantic search dependencies are missing. "
                "Install with: pip install -e '.[semantic]'"
            )

    @staticmethod
    def _format_pgvector_literal(vector: list[float]) -> str:
        if not vector:
            return "[]"
        values = ",".join(f"{float(value):.12g}" for value in vector)
        return f"[{values}]"

    async def _ensure_vector_tables(self) -> None:
        self._assert_semantic_available()
        if self._vector_tables_initialized:
            return

        async with self._vector_tables_lock:
            if self._vector_tables_initialized:
                return

            async with db.scoped_session(self.session_maker) as session:
                try:
                    await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                except Exception as exc:
                    raise SemanticDependenciesMissingError(
                        "pgvector extension is unavailable for this Postgres database."
                    ) from exc

                await session.execute(
                    text(
                        """
                        CREATE TABLE IF NOT EXISTS search_vector_chunks (
                            id BIGSERIAL PRIMARY KEY,
                            entity_id INTEGER NOT NULL,
                            project_id INTEGER NOT NULL,
                            chunk_key TEXT NOT NULL,
                            chunk_text TEXT NOT NULL,
                            source_hash TEXT NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            UNIQUE (project_id, entity_id, chunk_key)
                        )
                        """
                    )
                )
                await session.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS idx_search_vector_chunks_project_entity
                        ON search_vector_chunks (project_id, entity_id)
                        """
                    )
                )
                await session.execute(
                    text(
                        """
                        CREATE TABLE IF NOT EXISTS search_vector_embeddings (
                            chunk_id BIGINT PRIMARY KEY
                                REFERENCES search_vector_chunks(id) ON DELETE CASCADE,
                            project_id INTEGER NOT NULL,
                            embedding vector NOT NULL,
                            embedding_dims INTEGER NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                )
                await session.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS idx_search_vector_embeddings_project_dims
                        ON search_vector_embeddings (project_id, embedding_dims)
                        """
                    )
                )
                await session.commit()

            self._vector_tables_initialized = True

    def _split_text_into_chunks(self, text_value: str) -> list[str]:
        normalized = (text_value or "").strip()
        if not normalized:
            return []

        lines = normalized.splitlines()
        sections: list[str] = []
        current_section: list[str] = []
        for line in lines:
            # Trigger: markdown header encountered and we already accumulated content.
            # Why: preserve semantic structure of notes for chunk coherence.
            # Outcome: starts a new chunk section at header boundaries.
            if HEADER_LINE_PATTERN.match(line) and current_section:
                sections.append("\n".join(current_section).strip())
                current_section = [line]
            else:
                current_section.append(line)
        if current_section:
            sections.append("\n".join(current_section).strip())

        chunked_sections: list[str] = []
        current_chunk = ""

        for section in sections:
            if len(section) > MAX_VECTOR_CHUNK_CHARS:
                if current_chunk:
                    chunked_sections.append(current_chunk)
                    current_chunk = ""
                long_chunks = self._split_long_section(section)
                if long_chunks:
                    chunked_sections.extend(long_chunks[:-1])
                    current_chunk = long_chunks[-1]
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

    def _split_long_section(self, section_text: str) -> list[str]:
        paragraphs = [paragraph.strip() for paragraph in section_text.split("\n\n") if paragraph.strip()]
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

    def _compose_row_source_text(self, row) -> str:
        if row.type == SearchItemType.ENTITY.value:
            row_parts = [
                row.title or "",
                row.permalink or "",
                row.content_stems or "",
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

    async def _search_vector_only(
        self,
        *,
        search_text: str,
        permalink: Optional[str],
        permalink_match: Optional[str],
        title: Optional[str],
        types: Optional[List[str]],
        after_date: Optional[datetime],
        search_item_types: Optional[List[SearchItemType]],
        metadata_filters: Optional[dict],
        limit: int,
        offset: int,
    ) -> List[SearchIndexRow]:
        self._assert_semantic_available()
        await self._ensure_vector_tables()
        assert self._embedding_provider is not None

        query_embedding = await self._embedding_provider.embed_query(search_text.strip())
        if not query_embedding:
            return []

        embedding_dims = len(query_embedding)
        query_embedding_literal = self._format_pgvector_literal(query_embedding)
        candidate_limit = max(self._semantic_vector_k, (limit + offset) * 5)

        async with db.scoped_session(self.session_maker) as session:
            vector_result = await session.execute(
                text(
                    """
                    WITH vector_matches AS (
                        SELECT
                            e.chunk_id,
                            (e.embedding <=> CAST(:query_embedding AS vector)) AS distance
                        FROM search_vector_embeddings e
                        WHERE e.project_id = :project_id
                          AND e.embedding_dims = :embedding_dims
                        ORDER BY e.embedding <=> CAST(:query_embedding AS vector)
                        LIMIT :vector_k
                    )
                    SELECT c.entity_id, MIN(vector_matches.distance) AS best_distance
                    FROM vector_matches
                    JOIN search_vector_chunks c ON c.id = vector_matches.chunk_id
                    WHERE c.project_id = :project_id
                    GROUP BY c.entity_id
                    ORDER BY best_distance ASC
                    LIMIT :vector_k
                    """
                ),
                {
                    "query_embedding": query_embedding_literal,
                    "project_id": self.project_id,
                    "embedding_dims": embedding_dims,
                    "vector_k": candidate_limit,
                },
            )
            vector_rows = vector_result.mappings().all()

        if not vector_rows:
            return []

        similarity_by_entity_id: dict[int, float] = {}
        for row in vector_rows:
            entity_id = int(row["entity_id"])
            distance = float(row["best_distance"])
            similarity = 1.0 / (1.0 + max(distance, 0.0))
            current = similarity_by_entity_id.get(entity_id)
            if current is None or similarity > current:
                similarity_by_entity_id[entity_id] = similarity

        filter_requested = any(
            [
                permalink,
                permalink_match,
                title,
                types,
                after_date,
                search_item_types,
                metadata_filters,
            ]
        )

        entity_rows_by_id: dict[int, SearchIndexRow] = {}

        # Trigger: query includes non-text filters.
        # Why: keep filter semantics consistent between vector and FTS paths.
        # Outcome: vector similarity ranks only already-filtered entity candidates.
        if filter_requested:
            filtered_rows = await self.search(
                search_text=None,
                permalink=permalink,
                permalink_match=permalink_match,
                title=title,
                types=types,
                after_date=after_date,
                search_item_types=search_item_types,
                metadata_filters=metadata_filters,
                retrieval_mode=SearchRetrievalMode.FTS,
                limit=VECTOR_FILTER_SCAN_LIMIT,
                offset=0,
            )
            entity_rows_by_id = {
                row.entity_id: row
                for row in filtered_rows
                if row.type == SearchItemType.ENTITY.value and row.entity_id is not None
            }
        else:
            entity_ids = list(similarity_by_entity_id.keys())
            if entity_ids:
                placeholders = ", ".join(f":id_{idx}" for idx in range(len(entity_ids)))
                params = {
                    **{f"id_{idx}": entity_id for idx, entity_id in enumerate(entity_ids)},
                    "project_id": self.project_id,
                    "item_type": SearchItemType.ENTITY.value,
                }
                sql = f"""
                    SELECT
                        project_id,
                        id,
                        title,
                        permalink,
                        file_path,
                        type,
                        metadata,
                        from_id,
                        to_id,
                        relation_type,
                        entity_id,
                        content_snippet,
                        category,
                        created_at,
                        updated_at,
                        0 as score
                    FROM search_index
                    WHERE project_id = :project_id
                      AND type = :item_type
                      AND entity_id IN ({placeholders})
                """
                async with db.scoped_session(self.session_maker) as session:
                    row_result = await session.execute(text(sql), params)
                    for row in row_result.fetchall():
                        entity_rows_by_id[row.entity_id] = SearchIndexRow(
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

        ranked_rows: list[SearchIndexRow] = []
        for entity_id, similarity in similarity_by_entity_id.items():
            row = entity_rows_by_id.get(entity_id)
            if row is None:
                continue
            ranked_rows.append(replace(row, score=similarity))

        ranked_rows.sort(key=lambda item: item.score or 0.0, reverse=True)
        return ranked_rows[offset : offset + limit]

    async def _search_hybrid(
        self,
        *,
        search_text: str,
        permalink: Optional[str],
        permalink_match: Optional[str],
        title: Optional[str],
        types: Optional[List[str]],
        after_date: Optional[datetime],
        search_item_types: Optional[List[SearchItemType]],
        metadata_filters: Optional[dict],
        limit: int,
        offset: int,
    ) -> List[SearchIndexRow]:
        self._assert_semantic_available()
        candidate_limit = max(self._semantic_vector_k, (limit + offset) * 10)
        fts_results = await self.search(
            search_text=search_text,
            permalink=permalink,
            permalink_match=permalink_match,
            title=title,
            types=types,
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
            types=types,
            after_date=after_date,
            search_item_types=search_item_types,
            metadata_filters=metadata_filters,
            limit=candidate_limit,
            offset=0,
        )

        fused_scores: dict[str, float] = {}
        rows_by_permalink: dict[str, SearchIndexRow] = {}

        for rank, row in enumerate(fts_results, start=1):
            if not row.permalink:
                continue
            fused_scores[row.permalink] = fused_scores.get(row.permalink, 0.0) + (
                1.0 / (RRF_K + rank)
            )
            rows_by_permalink[row.permalink] = row

        for rank, row in enumerate(vector_results, start=1):
            if not row.permalink:
                continue
            fused_scores[row.permalink] = fused_scores.get(row.permalink, 0.0) + (
                1.0 / (RRF_K + rank)
            )
            rows_by_permalink[row.permalink] = row

        ranked = sorted(fused_scores.items(), key=lambda item: item[1], reverse=True)
        output: list[SearchIndexRow] = []
        for permalink_value, fused_score in ranked[offset : offset + limit]:
            output.append(replace(rows_by_permalink[permalink_value], score=fused_score))
        return output

    async def search(
        self,
        search_text: Optional[str] = None,
        permalink: Optional[str] = None,
        permalink_match: Optional[str] = None,
        title: Optional[str] = None,
        types: Optional[List[str]] = None,
        after_date: Optional[datetime] = None,
        search_item_types: Optional[List[SearchItemType]] = None,
        metadata_filters: Optional[dict] = None,
        retrieval_mode: SearchRetrievalMode = SearchRetrievalMode.FTS,
        limit: int = 10,
        offset: int = 0,
    ) -> List[SearchIndexRow]:
        """Search across all indexed content using PostgreSQL tsvector."""
        mode = (
            retrieval_mode.value
            if isinstance(retrieval_mode, SearchRetrievalMode)
            else str(retrieval_mode)
        )
        can_use_vector = (
            bool(search_text)
            and bool(search_text.strip())
            and search_text.strip() != "*"
            and not permalink
            and not permalink_match
            and not title
        )
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
                types=types,
                after_date=after_date,
                search_item_types=search_item_types,
                metadata_filters=metadata_filters,
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
                types=types,
                after_date=after_date,
                search_item_types=search_item_types,
                metadata_filters=metadata_filters,
                limit=limit,
                offset=offset,
            )

        conditions = []
        params = {}
        order_by_clause = ""
        from_clause = "search_index"

        # Handle text search for title and content using tsvector
        if search_text:
            if search_text.strip() == "*" or search_text.strip() == "":
                # For wildcard searches, don't add any text conditions
                pass
            else:
                # Prepare search term for tsquery
                processed_text = self._prepare_search_term(search_text.strip())
                params["text"] = processed_text
                # Use @@ operator for tsvector matching
                conditions.append(
                    "search_index.textsearchable_index_col @@ to_tsquery('english', :text)"
                )

        # Handle title search
        if title:
            title_text = self._prepare_search_term(title.strip(), is_prefix=False)
            params["title_text"] = title_text
            conditions.append(
                "to_tsvector('english', search_index.title) @@ to_tsquery('english', :title_text)"
            )

        # Handle permalink exact search
        if permalink:
            params["permalink"] = permalink
            conditions.append("search_index.permalink = :permalink")

        # Handle permalink pattern match
        if permalink_match:
            permalink_text = permalink_match.lower().strip()
            params["permalink"] = permalink_text
            if "*" in permalink_match:
                # Use LIKE for pattern matching in Postgres
                # Convert * to % for SQL LIKE
                permalink_pattern = permalink_text.replace("*", "%")
                params["permalink"] = permalink_pattern
                conditions.append("search_index.permalink LIKE :permalink")
            else:
                conditions.append("search_index.permalink = :permalink")

        # Handle search item type filter
        if search_item_types:
            type_list = ", ".join(f"'{t.value}'" for t in search_item_types)
            conditions.append(f"search_index.type IN ({type_list})")

        # Handle entity type filter using JSONB containment
        if types:
            # Use JSONB @> operator for efficient containment queries
            type_conditions = []
            for entity_type in types:
                # Create JSONB containment condition for each type
                type_conditions.append(
                    f'search_index.metadata @> \'{{"entity_type": "{entity_type}"}}\''
                )
            conditions.append(f"({' OR '.join(type_conditions)})")

        # Handle date filter
        if after_date:
            params["after_date"] = after_date
            conditions.append("search_index.created_at > :after_date")
            # order by most recent first
            order_by_clause = ", search_index.updated_at DESC"

        # Handle structured metadata filters (frontmatter)
        if metadata_filters:
            parsed_filters = parse_metadata_filters(metadata_filters)
            from_clause = "search_index JOIN entity ON search_index.entity_id = entity.id"
            metadata_expr = "entity.entity_metadata::jsonb"

            for idx, filt in enumerate(parsed_filters):
                path = build_postgres_json_path(filt.path_parts)
                text_expr = f"({metadata_expr} #>> '{path}')"
                json_expr = f"({metadata_expr} #> '{path}')"

                if filt.op == "eq":
                    value_param = f"meta_val_{idx}"
                    params[value_param] = filt.value
                    conditions.append(f"{text_expr} = :{value_param}")
                    continue

                if filt.op == "in":
                    placeholders = []
                    for j, val in enumerate(filt.value):
                        value_param = f"meta_val_{idx}_{j}"
                        params[value_param] = val
                        placeholders.append(f":{value_param}")
                    conditions.append(f"{text_expr} IN ({', '.join(placeholders)})")
                    continue

                if filt.op == "contains":
                    import json as _json

                    base_param = f"meta_val_{idx}"
                    tag_conditions = []
                    # Require all values to be present
                    for j, val in enumerate(filt.value):
                        tag_param = f"{base_param}_{j}"
                        params[tag_param] = _json.dumps([val])
                        like_param = f"{base_param}_{j}_like"
                        params[like_param] = f'%"{val}"%'
                        like_param_single = f"{base_param}_{j}_like_single"
                        params[like_param_single] = f"%'{val}'%"
                        tag_conditions.append(
                            f"({json_expr} @> CAST(:{tag_param} AS jsonb) "
                            f"OR {text_expr} LIKE :{like_param} "
                            f"OR {text_expr} LIKE :{like_param_single})"
                        )
                    conditions.append(" AND ".join(tag_conditions))
                    continue

                if filt.op in {"gt", "gte", "lt", "lte", "between"}:
                    compare_expr = (
                        f"({metadata_expr} #>> '{path}')::double precision"
                        if filt.comparison == "numeric"
                        else text_expr
                    )

                    if filt.op == "between":
                        min_param = f"meta_val_{idx}_min"
                        max_param = f"meta_val_{idx}_max"
                        params[min_param] = filt.value[0]
                        params[max_param] = filt.value[1]
                        conditions.append(f"{compare_expr} BETWEEN :{min_param} AND :{max_param}")
                    else:
                        value_param = f"meta_val_{idx}"
                        params[value_param] = filt.value
                        operator = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[filt.op]
                        conditions.append(f"{compare_expr} {operator} :{value_param}")
                    continue

        # Always filter by project_id
        params["project_id"] = self.project_id
        conditions.append("search_index.project_id = :project_id")

        # set limit and offset
        params["limit"] = limit
        params["offset"] = offset

        # Build WHERE clause
        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Build SQL with ts_rank() for scoring
        # Note: If no text search, score will be NULL, so we use COALESCE to default to 0
        if search_text and search_text.strip() and search_text.strip() != "*":
            score_expr = (
                "ts_rank(search_index.textsearchable_index_col, to_tsquery('english', :text))"
            )
        else:
            score_expr = "0"

        sql = f"""
            SELECT
                search_index.project_id,
                search_index.id,
                search_index.title,
                search_index.permalink,
                search_index.file_path,
                search_index.type,
                search_index.metadata,
                search_index.from_id,
                search_index.to_id,
                search_index.relation_type,
                search_index.entity_id,
                search_index.content_snippet,
                search_index.category,
                search_index.created_at,
                search_index.updated_at,
                {score_expr} as score
            FROM {from_clause}
            WHERE {where_clause}
            ORDER BY score DESC, search_index.id ASC {order_by_clause}
            LIMIT :limit
            OFFSET :offset
        """

        logger.trace(f"Search {sql} params: {params}")
        try:
            async with db.scoped_session(self.session_maker) as session:
                result = await session.execute(text(sql), params)
                rows = result.fetchall()
        except Exception as e:
            # Handle tsquery syntax errors (and only those).
            #
            # Important: Postgres errors for other failures (e.g. missing table) will still mention
            # `to_tsquery(...)` in the SQL text, so checking for the substring "tsquery" is too broad.
            msg = str(e).lower()
            if (
                "syntax error in tsquery" in msg
                or "invalid input syntax for type tsquery" in msg
                or "no operand in tsquery" in msg
                or "no operator in tsquery" in msg
            ):
                logger.warning(f"tsquery syntax error for search term: {search_text}, error: {e}")
                return []

            # Re-raise other database errors
            logger.error(f"Database error during search: {e}")
            raise

        results = [
            SearchIndexRow(
                project_id=self.project_id,
                id=row.id,
                title=row.title,
                permalink=row.permalink,
                file_path=row.file_path,
                type=row.type,
                score=float(row.score) if row.score else 0.0,
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
            for row in rows
        ]

        logger.trace(f"Found {len(results)} search results")
        for r in results:
            logger.trace(
                f"Search result: project_id: {r.project_id} type:{r.type} title: {r.title} permalink: {r.permalink} score: {r.score}"
            )

        return results

    async def bulk_index_items(self, search_index_rows: List[SearchIndexRow]) -> None:
        """Index multiple items in a single batch operation using UPSERT.

        Uses INSERT ... ON CONFLICT to handle race conditions during parallel
        entity indexing. The partial unique index uix_search_index_permalink_project
        on (permalink, project_id) WHERE permalink IS NOT NULL prevents duplicate
        permalinks.

        For rows with non-null permalinks (entities), conflicts are resolved by
        updating the existing row. For rows with null permalinks (observations,
        relations), the partial index doesn't apply and they are inserted directly.

        Args:
            search_index_rows: List of SearchIndexRow objects to index
        """

        if not search_index_rows:
            return

        async with db.scoped_session(self.session_maker) as session:
            # When using text() raw SQL, always serialize JSON to string
            # Both SQLite (TEXT) and Postgres (JSONB) accept JSON strings in raw SQL
            # The database driver/column type will handle conversion
            insert_data_list = []
            for row in search_index_rows:
                insert_data = row.to_insert(serialize_json=True)
                insert_data["project_id"] = self.project_id
                insert_data_list.append(insert_data)

            # Use upsert to handle race conditions during parallel indexing
            # ON CONFLICT (permalink, project_id) matches the partial unique index
            # uix_search_index_permalink_project WHERE permalink IS NOT NULL
            # For rows with NULL permalinks (observations, relations), no conflict occurs
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
                    ON CONFLICT (permalink, project_id) WHERE permalink IS NOT NULL DO UPDATE SET
                        id = EXCLUDED.id,
                        title = EXCLUDED.title,
                        content_stems = EXCLUDED.content_stems,
                        content_snippet = EXCLUDED.content_snippet,
                        file_path = EXCLUDED.file_path,
                        type = EXCLUDED.type,
                        metadata = EXCLUDED.metadata,
                        from_id = EXCLUDED.from_id,
                        to_id = EXCLUDED.to_id,
                        relation_type = EXCLUDED.relation_type,
                        entity_id = EXCLUDED.entity_id,
                        category = EXCLUDED.category,
                        created_at = EXCLUDED.created_at,
                        updated_at = EXCLUDED.updated_at
                """),
                insert_data_list,
            )
            logger.debug(f"Bulk indexed {len(search_index_rows)} rows")
            await session.commit()

    async def sync_entity_vectors(self, entity_id: int) -> None:
        """Sync semantic chunk rows + pgvector embeddings for a single entity."""
        self._assert_semantic_available()
        await self._ensure_vector_tables()
        assert self._embedding_provider is not None

        async with db.scoped_session(self.session_maker) as session:
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

            if not rows:
                await session.execute(
                    text(
                        "DELETE FROM search_vector_chunks "
                        "WHERE project_id = :project_id AND entity_id = :entity_id"
                    ),
                    {"project_id": self.project_id, "entity_id": entity_id},
                )
                await session.commit()
                return

            chunk_records = self._build_chunk_records(rows)
            if not chunk_records:
                await session.execute(
                    text(
                        "DELETE FROM search_vector_chunks "
                        "WHERE project_id = :project_id AND entity_id = :entity_id"
                    ),
                    {"project_id": self.project_id, "entity_id": entity_id},
                )
                await session.commit()
                return

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
            stale_row_ids = [
                int(row.id)
                for chunk_key, row in existing_by_key.items()
                if chunk_key not in incoming_hashes
            ]

            if stale_row_ids:
                stale_placeholders = ", ".join(
                    f":stale_id_{idx}" for idx in range(len(stale_row_ids))
                )
                stale_params = {
                    "project_id": self.project_id,
                    "entity_id": entity_id,
                    **{f"stale_id_{idx}": row_id for idx, row_id in enumerate(stale_row_ids)},
                }
                await session.execute(
                    text(
                        "DELETE FROM search_vector_chunks "
                        f"WHERE id IN ({stale_placeholders}) "
                        "AND project_id = :project_id AND entity_id = :entity_id"
                    ),
                    stale_params,
                )

            embedding_jobs: list[tuple[int, str]] = []
            for record in chunk_records:
                current = existing_by_key.get(record["chunk_key"])
                if current and current.source_hash == record["source_hash"]:
                    continue

                if current:
                    row_id = int(current.id)
                    await session.execute(
                        text(
                            "UPDATE search_vector_chunks "
                            "SET chunk_text = :chunk_text, source_hash = :source_hash, "
                            "updated_at = NOW() "
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
                        ":entity_id, :project_id, :chunk_key, :chunk_text, :source_hash, NOW()"
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

        texts = [item[1] for item in embedding_jobs]
        embeddings = await self._embedding_provider.embed_documents(texts)
        if len(embeddings) != len(embedding_jobs):
            raise RuntimeError("Embedding provider returned an unexpected number of vectors.")
        if embeddings:
            self._vector_dimensions = len(embeddings[0])

        async with db.scoped_session(self.session_maker) as session:
            for (row_id, _), vector in zip(embedding_jobs, embeddings, strict=False):
                vector_literal = self._format_pgvector_literal(vector)
                await session.execute(
                    text(
                        "INSERT INTO search_vector_embeddings ("
                        "chunk_id, project_id, embedding, embedding_dims, updated_at"
                        ") VALUES ("
                        ":chunk_id, :project_id, CAST(:embedding AS vector), :embedding_dims, NOW()"
                        ") "
                        "ON CONFLICT (chunk_id) DO UPDATE SET "
                        "project_id = EXCLUDED.project_id, "
                        "embedding = EXCLUDED.embedding, "
                        "embedding_dims = EXCLUDED.embedding_dims, "
                        "updated_at = NOW()"
                    ),
                    {
                        "chunk_id": row_id,
                        "project_id": self.project_id,
                        "embedding": vector_literal,
                        "embedding_dims": len(vector),
                    },
                )
            await session.commit()
