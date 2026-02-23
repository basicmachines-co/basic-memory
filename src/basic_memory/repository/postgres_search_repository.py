"""PostgreSQL tsvector-based search repository implementation."""

import asyncio
import json
import re
from datetime import datetime
from typing import List, Optional

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.embedding_provider_factory import create_embedding_provider
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_repository_base import SearchRepositoryBase
from basic_memory.repository.metadata_filters import (
    parse_metadata_filters,
    build_postgres_json_path,
)
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode


def _strip_nul_from_row(row_data: dict) -> dict:
    """Strip NUL bytes from all string values in a row dict.

    Secondary defense: PostgreSQL text columns cannot store \\x00.
    Primary sanitization happens in SearchService.index_entity_markdown().
    """
    return {k: v.replace("\x00", "") if isinstance(v, str) else v for k, v in row_data.items()}


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
        self._semantic_min_similarity = self._app_config.semantic_min_similarity
        self._embedding_provider = embedding_provider
        self._vector_dimensions = 384
        self._vector_tables_initialized = False
        self._vector_tables_lock = asyncio.Lock()

        if self._semantic_enabled and self._embedding_provider is None:
            self._embedding_provider = create_embedding_provider(self._app_config)
        if self._embedding_provider is not None:
            self._vector_dimensions = self._embedding_provider.dimensions

    async def init_search_index(self):
        """Create Postgres table with tsvector column and GIN indexes.

        Note: FTS schema is handled by Alembic migrations. Vector tables are
        created here at startup so missing pgvector or provider errors surface
        immediately.
        """
        logger.info("PostgreSQL search index initialization handled by migrations")

        # Fail fast: create vector tables at startup so missing pgvector
        # or embedding provider errors surface immediately
        if self._semantic_enabled:
            await self._ensure_vector_tables()

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
            insert_data = _strip_nul_from_row(insert_data)

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

    # ------------------------------------------------------------------
    # tsquery preparation (backend-specific)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # pgvector utility
    # ------------------------------------------------------------------

    @staticmethod
    def _format_pgvector_literal(vector: list[float]) -> str:
        if not vector:
            return "[]"
        values = ",".join(f"{float(value):.12g}" for value in vector)
        return f"[{values}]"

    # ------------------------------------------------------------------
    # Abstract hook implementations (vector/semantic, Postgres-specific)
    # ------------------------------------------------------------------

    async def _ensure_vector_tables(self) -> None:
        self._assert_semantic_available()
        if self._vector_tables_initialized:
            return

        logger.info("Ensuring Postgres vector tables exist for semantic search")

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

                # --- Chunks table (dimension-independent, may already exist via migration) ---
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

                # --- Embeddings table (dimension-dependent, created at runtime) ---
                # Trigger: provider dimensions may differ from what was previously deployed.
                # Why: the column type `vector(N)` is fixed at table creation; switching
                # from FastEmbed (384) to OpenAI (1536) requires recreation.
                # Outcome: mismatched table is dropped and recreated with correct dims.
                # Embeddings are derived data — re-indexing will repopulate them.
                existing_dims = await self._get_existing_embedding_dims(session)
                if existing_dims is not None and existing_dims != self._vector_dimensions:
                    logger.warning(
                        f"Embedding dimension mismatch: table has {existing_dims}, "
                        f"provider expects {self._vector_dimensions}. "
                        "Dropping and recreating search_vector_embeddings."
                    )
                    await session.execute(text("DROP TABLE IF EXISTS search_vector_embeddings"))

                await session.execute(
                    text(
                        f"""
                        CREATE TABLE IF NOT EXISTS search_vector_embeddings (
                            chunk_id BIGINT PRIMARY KEY
                                REFERENCES search_vector_chunks(id) ON DELETE CASCADE,
                            project_id INTEGER NOT NULL,
                            embedding vector({self._vector_dimensions}) NOT NULL,
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
                # HNSW index for approximate nearest-neighbour search.
                # Without this every vector query is a sequential scan.
                await session.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS idx_search_vector_embeddings_hnsw
                        ON search_vector_embeddings
                        USING hnsw (embedding vector_cosine_ops)
                        WITH (m = 16, ef_construction = 64)
                        """
                    )
                )
                await session.commit()

            logger.info(f"Postgres vector tables ready (dimensions={self._vector_dimensions})")
            self._vector_tables_initialized = True

    async def _get_existing_embedding_dims(self, session: AsyncSession) -> int | None:
        """Query the vector column dimension from an existing search_vector_embeddings table.

        Returns None when the table does not exist.
        Uses information_schema to avoid regclass cast errors on missing tables,
        then reads atttypmod from pg_attribute for the actual dimension value.
        """
        # Check table existence via information_schema (no exception on missing)
        exists_result = await session.execute(
            text(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'search_vector_embeddings'
                """
            )
        )
        if exists_result.fetchone() is None:
            return None

        result = await session.execute(
            text(
                """
                SELECT atttypmod
                FROM pg_attribute
                WHERE attrelid = 'search_vector_embeddings'::regclass
                  AND attname = 'embedding'
                """
            )
        )
        row = result.fetchone()
        if row is None:
            return None
        # pgvector stores dimensions in atttypmod directly
        return int(row[0])

    async def _run_vector_query(
        self,
        session: AsyncSession,
        query_embedding: list[float],
        candidate_limit: int,
    ) -> list[dict]:
        if not query_embedding:
            return []

        embedding_dims = len(query_embedding)
        query_embedding_literal = self._format_pgvector_literal(query_embedding)

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
                SELECT c.entity_id, c.chunk_key, c.chunk_text, vector_matches.distance AS best_distance
                FROM vector_matches
                JOIN search_vector_chunks c ON c.id = vector_matches.chunk_id
                WHERE c.project_id = :project_id
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
        return [dict(row) for row in vector_result.mappings().all()]

    async def _write_embeddings(
        self,
        session: AsyncSession,
        jobs: list[tuple[int, str]],
        embeddings: list[list[float]],
    ) -> None:
        for (row_id, _), vector in zip(jobs, embeddings, strict=True):
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

    async def _delete_entity_chunks(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> None:
        # Postgres has ON DELETE CASCADE from embeddings → chunks
        await session.execute(
            text(
                "DELETE FROM search_vector_chunks "
                "WHERE project_id = :project_id AND entity_id = :entity_id"
            ),
            {"project_id": self.project_id, "entity_id": entity_id},
        )

    async def _delete_stale_chunks(
        self,
        session: AsyncSession,
        stale_ids: list[int],
        entity_id: int,
    ) -> None:
        stale_placeholders = ", ".join(f":stale_id_{idx}" for idx in range(len(stale_ids)))
        stale_params = {
            "project_id": self.project_id,
            "entity_id": entity_id,
            **{f"stale_id_{idx}": row_id for idx, row_id in enumerate(stale_ids)},
        }
        # CASCADE handles embedding deletion
        await session.execute(
            text(
                "DELETE FROM search_vector_chunks "
                f"WHERE id IN ({stale_placeholders}) "
                "AND project_id = :project_id AND entity_id = :entity_id"
            ),
            stale_params,
        )

    async def _update_timestamp_sql(self) -> str:
        return "NOW()"  # pragma: no cover

    def _distance_to_similarity(self, distance: float) -> float:
        """Convert pgvector cosine distance to cosine similarity.

        pgvector's <=> operator returns cosine distance in [0, 2],
        where cos_distance = 1 - cos_similarity.
        """
        return max(0.0, 1.0 - distance)

    def _timestamp_now_expr(self) -> str:
        return "NOW()"

    # ------------------------------------------------------------------
    # Index / bulk index overrides (Postgres UPSERT)
    # ------------------------------------------------------------------

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
                insert_data_list.append(_strip_nul_from_row(insert_data))

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

    # ------------------------------------------------------------------
    # FTS search (Postgres-specific)
    # ------------------------------------------------------------------

    async def search(
        self,
        search_text: Optional[str] = None,
        permalink: Optional[str] = None,
        permalink_match: Optional[str] = None,
        title: Optional[str] = None,
        note_types: Optional[List[str]] = None,
        after_date: Optional[datetime] = None,
        search_item_types: Optional[List[SearchItemType]] = None,
        metadata_filters: Optional[dict] = None,
        retrieval_mode: SearchRetrievalMode = SearchRetrievalMode.FTS,
        min_similarity: Optional[float] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> List[SearchIndexRow]:
        """Search across all indexed content using PostgreSQL tsvector."""
        # --- Dispatch vector / hybrid modes (shared logic) ---
        dispatched = await self._dispatch_retrieval_mode(
            search_text=search_text,
            permalink=permalink,
            permalink_match=permalink_match,
            title=title,
            note_types=note_types,
            after_date=after_date,
            search_item_types=search_item_types,
            metadata_filters=metadata_filters,
            retrieval_mode=retrieval_mode,
            min_similarity=min_similarity,
            limit=limit,
            offset=offset,
        )
        if dispatched is not None:
            return dispatched

        # --- FTS mode (Postgres-specific) ---
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

        # Handle note type filter using JSONB containment (frontmatter type field)
        if note_types:
            # Use JSONB @> operator for efficient containment queries
            type_conditions = []
            for note_type in note_types:
                # Create JSONB containment condition for each note type
                type_conditions.append(
                    f'search_index.metadata @> \'{{"note_type": "{note_type}"}}\''
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
