"""SQLite FTS5-based search repository implementation."""

import json
import re
from datetime import datetime
from typing import List, Optional

import asyncio
from loguru import logger
from sqlalchemy import text
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.models.search import (
    CREATE_SEARCH_INDEX,
    CREATE_SQLITE_SEARCH_VECTOR_CHUNKS,
    CREATE_SQLITE_SEARCH_VECTOR_CHUNKS_PROJECT_ENTITY,
    CREATE_SQLITE_SEARCH_VECTOR_CHUNKS_UNIQUE,
    create_sqlite_search_vector_embeddings,
)
from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.embedding_provider_factory import create_embedding_provider
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_repository_base import SearchRepositoryBase
from basic_memory.repository.metadata_filters import parse_metadata_filters, build_sqlite_json_path
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode


class SQLiteSearchRepository(SearchRepositoryBase):
    """SQLite FTS5 implementation of search repository.

    Uses SQLite's FTS5 virtual tables for full-text search with:
    - MATCH operator for queries
    - bm25() function for relevance scoring
    - Special character quoting for syntax safety
    - Prefix wildcard matching with *
    """

    def __init__(
        self,
        session_maker,
        project_id: int,
        app_config: BasicMemoryConfig | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ):
        super().__init__(session_maker, project_id)
        self._entity_columns: set[str] | None = None
        self._app_config = app_config or ConfigManager().config
        self._semantic_enabled = self._app_config.semantic_search_enabled
        self._semantic_vector_k = self._app_config.semantic_vector_k
        self._semantic_min_similarity = self._app_config.semantic_min_similarity
        self._embedding_provider = embedding_provider
        self._sqlite_vec_lock = asyncio.Lock()
        self._vector_tables_initialized = False
        self._vector_dimensions = 384

        if self._semantic_enabled and self._embedding_provider is None:
            # Constraint: SQLite maps L2 distance to cosine similarity via 1 - L2²/2.
            # This conversion is correct only for unit-normalized embeddings.
            # Provider implementations must return normalized vectors.
            self._embedding_provider = create_embedding_provider(self._app_config)
        if self._embedding_provider is not None:
            self._vector_dimensions = self._embedding_provider.dimensions

    async def _get_entity_columns(self) -> set[str]:
        if self._entity_columns is None:
            async with db.scoped_session(self.session_maker) as session:
                result = await session.execute(text("PRAGMA table_info(entity)"))
                self._entity_columns = {row[1] for row in result.fetchall()}
        return self._entity_columns

    async def init_search_index(self):
        """Create FTS5 virtual table for search if it doesn't exist.

        Uses CREATE VIRTUAL TABLE IF NOT EXISTS to preserve existing indexed data
        across server restarts. Also creates vector tables when semantic search
        is enabled so missing dependencies are caught at startup, not first query.
        """
        logger.info("Initializing SQLite FTS5 search index")
        try:
            async with db.scoped_session(self.session_maker) as session:
                # Create FTS5 virtual table if it doesn't exist
                await session.execute(CREATE_SEARCH_INDEX)
                await session.commit()
        except Exception as e:  # pragma: no cover
            logger.error(f"Error initializing search index: {e}")
            raise e

        # Fail fast: create vector tables at startup so missing sqlite-vec
        # or embedding provider errors surface immediately
        if self._semantic_enabled:
            await self._ensure_vector_tables()

    # ------------------------------------------------------------------
    # FTS5 query preparation (backend-specific)
    # ------------------------------------------------------------------

    def _prepare_boolean_query(self, query: str) -> str:
        """Prepare a Boolean query by quoting individual terms while preserving operators.

        Args:
            query: A Boolean query like "tier1-test AND unicode" or "(hello OR world) NOT test"

        Returns:
            A properly formatted Boolean query with quoted terms that need quoting
        """
        # Define Boolean operators and their boundaries
        boolean_pattern = r"(\bAND\b|\bOR\b|\bNOT\b)"

        # Split the query by Boolean operators, keeping the operators
        parts = re.split(boolean_pattern, query)

        processed_parts = []
        for part in parts:
            part = part.strip()
            if not part:
                continue

            # If it's a Boolean operator, keep it as is
            if part in ["AND", "OR", "NOT"]:
                processed_parts.append(part)
            else:
                # Handle parentheses specially - they should be preserved for grouping
                if "(" in part or ")" in part:
                    # Parse parenthetical expressions carefully
                    processed_part = self._prepare_parenthetical_term(part)
                    processed_parts.append(processed_part)
                else:
                    # This is a search term - for Boolean queries, don't add prefix wildcards
                    prepared_term = self._prepare_single_term(part, is_prefix=False)
                    processed_parts.append(prepared_term)

        return " ".join(processed_parts)

    def _prepare_parenthetical_term(self, term: str) -> str:
        """Prepare a term that contains parentheses, preserving the parentheses for grouping.

        Args:
            term: A term that may contain parentheses like "(hello" or "world)" or "(hello OR world)"

        Returns:
            A properly formatted term with parentheses preserved
        """
        # Handle terms that start/end with parentheses but may contain quotable content
        result = ""
        i = 0
        while i < len(term):
            if term[i] in "()":
                # Preserve parentheses as-is
                result += term[i]
                i += 1
            else:
                # Find the next parenthesis or end of string
                start = i
                while i < len(term) and term[i] not in "()":
                    i += 1

                # Extract the content between parentheses
                content = term[start:i].strip()
                if content:
                    # Only quote if it actually needs quoting (has hyphens, special chars, etc)
                    # but don't quote if it's just simple words
                    if self._needs_quoting(content):
                        escaped_content = content.replace('"', '""')
                        result += f'"{escaped_content}"'
                    else:
                        result += content

        return result

    def _needs_quoting(self, term: str) -> bool:
        """Check if a term needs to be quoted for FTS5 safety.

        Args:
            term: The term to check

        Returns:
            True if the term should be quoted
        """
        if not term or not term.strip():
            return False

        # Characters that indicate we should quote (excluding parentheses which are valid syntax)
        needs_quoting_chars = [
            " ",
            ".",
            ":",
            ";",
            ",",
            "<",
            ">",
            "?",
            "/",
            "-",
            "'",
            '"',
            "[",
            "]",
            "{",
            "}",
            "+",
            "!",
            "@",
            "#",
            "$",
            "%",
            "^",
            "&",
            "=",
            "|",
            "\\",
            "~",
            "`",
        ]

        return any(c in term for c in needs_quoting_chars)

    def _prepare_single_term(self, term: str, is_prefix: bool = True) -> str:
        """Prepare a single search term (no Boolean operators).

        Args:
            term: A single search term
            is_prefix: Whether to add prefix search capability (* suffix)

        Returns:
            A properly formatted single term
        """
        if not term or not term.strip():
            return term

        term = term.strip()

        # Check if term is already a proper wildcard pattern (alphanumeric + *)
        # e.g., "hello*", "test*world" - these should be left alone
        if "*" in term and all(c.isalnum() or c in "*_-" for c in term):
            return term

        # Characters that can cause FTS5 syntax errors when used as operators
        # We're more conservative here - only quote when we detect problematic patterns
        problematic_chars = [
            '"',
            "'",
            "(",
            ")",
            "[",
            "]",
            "{",
            "}",
            "+",
            "!",
            "@",
            "#",
            "$",
            "%",
            "^",
            "&",
            "=",
            "|",
            "\\",
            "~",
            "`",
        ]

        # Characters that indicate we should quote (spaces, dots, colons, etc.)
        # Adding hyphens here because FTS5 can have issues with hyphens followed by wildcards
        needs_quoting_chars = [" ", ".", ":", ";", ",", "<", ">", "?", "/", "-"]

        # Check if term needs quoting
        has_problematic = any(c in term for c in problematic_chars)
        has_spaces_or_special = any(c in term for c in needs_quoting_chars)

        if has_problematic or has_spaces_or_special:
            # Handle multi-word queries differently from special character queries
            if " " in term and not any(c in term for c in problematic_chars):
                # Check if any individual word contains special characters that need quoting
                words = term.strip().split()
                has_special_in_words = any(
                    any(c in word for c in needs_quoting_chars if c != " ") for word in words
                )

                if not has_special_in_words:
                    # For multi-word queries with simple words (like "emoji unicode"),
                    # use boolean AND to handle word order variations
                    if is_prefix:
                        # Add prefix wildcard to each word for better matching
                        prepared_words = [f"{word}*" for word in words if word]
                    else:
                        prepared_words = words
                    term = " AND ".join(prepared_words)
                else:
                    # If any word has special characters, quote the entire phrase
                    escaped_term = term.replace('"', '""')
                    if is_prefix and not ("/" in term and term.endswith(".md")):
                        term = f'"{escaped_term}"*'
                    else:
                        term = f'"{escaped_term}"'  # pragma: no cover
            else:
                # For terms with problematic characters or file paths, use exact phrase matching
                # Escape any existing quotes by doubling them
                escaped_term = term.replace('"', '""')
                # Quote the entire term to handle special characters safely
                if is_prefix and not ("/" in term and term.endswith(".md")):
                    # For search terms (not file paths), add prefix matching
                    term = f'"{escaped_term}"*'
                else:
                    # For file paths, use exact matching
                    term = f'"{escaped_term}"'
        elif is_prefix:
            # Only add wildcard for simple terms without special characters
            term = f"{term}*"

        return term

    def _prepare_search_term(self, term: str, is_prefix: bool = True) -> str:
        """Prepare a search term for FTS5 query.

        Args:
            term: The search term to prepare
            is_prefix: Whether to add prefix search capability (* suffix)

        For FTS5:
        - Boolean operators (AND, OR, NOT) are preserved for complex queries
        - Terms with FTS5 special characters are quoted to prevent syntax errors
        - Simple terms get prefix wildcards for better matching
        """
        # Check for explicit boolean operators - if present, process as Boolean query
        boolean_operators = [" AND ", " OR ", " NOT "]
        if any(op in f" {term} " for op in boolean_operators):
            return self._prepare_boolean_query(term)

        # For non-Boolean queries, use the single term preparation logic
        return self._prepare_single_term(term, is_prefix)

    # ------------------------------------------------------------------
    # sqlite-vec extension loading (SQLite-specific)
    # ------------------------------------------------------------------

    async def _ensure_sqlite_vec_loaded(self, session) -> None:
        try:
            await session.execute(text("SELECT vec_version()"))
            return
        except SAOperationalError:
            pass

        try:
            import sqlite_vec  # type: ignore[import-not-found]
        except ImportError as exc:
            raise SemanticDependenciesMissingError(
                "sqlite-vec package is missing. "
                "Install/update basic-memory to include semantic dependencies: "
                "pip install -U basic-memory"
            ) from exc

        async with self._sqlite_vec_lock:
            try:
                await session.execute(text("SELECT vec_version()"))
                return
            except SAOperationalError:
                pass

            async_connection = await session.connection()
            raw_connection = await async_connection.get_raw_connection()
            driver_connection = raw_connection.driver_connection
            await driver_connection.enable_load_extension(True)
            await driver_connection.load_extension(sqlite_vec.loadable_path())
            await driver_connection.enable_load_extension(False)
            await session.execute(text("SELECT vec_version()"))

    # ------------------------------------------------------------------
    # Abstract hook implementations (vector/semantic, SQLite-specific)
    # ------------------------------------------------------------------

    async def _ensure_vector_tables(self) -> None:
        self._assert_semantic_available()
        if self._vector_tables_initialized:
            return

        logger.info("Ensuring SQLite vector tables exist for semantic search")

        async with db.scoped_session(self.session_maker) as session:
            await self._ensure_sqlite_vec_loaded(session)

            chunks_columns_result = await session.execute(
                text("PRAGMA table_info(search_vector_chunks)")
            )
            chunks_columns = [row[1] for row in chunks_columns_result.fetchall()]

            expected_columns = {
                "id",
                "entity_id",
                "project_id",
                "chunk_key",
                "chunk_text",
                "source_hash",
                "updated_at",
            }
            schema_mismatch = bool(chunks_columns) and set(chunks_columns) != expected_columns
            if schema_mismatch:
                logger.warning("search_vector_chunks schema mismatch, recreating vector tables")
                await session.execute(text("DROP TABLE IF EXISTS search_vector_embeddings"))
                await session.execute(text("DROP TABLE IF EXISTS search_vector_chunks"))

            await session.execute(CREATE_SQLITE_SEARCH_VECTOR_CHUNKS)
            await session.execute(CREATE_SQLITE_SEARCH_VECTOR_CHUNKS_PROJECT_ENTITY)
            await session.execute(CREATE_SQLITE_SEARCH_VECTOR_CHUNKS_UNIQUE)

            # Trigger: legacy table from previous semantic implementation exists.
            # Why: old schema stores JSON vectors in a normal table and conflicts with sqlite-vec.
            # Outcome: remove disposable derived data so chunk/vector schema is deterministic.
            await session.execute(text("DROP TABLE IF EXISTS search_vector_index"))

            vector_sql_result = await session.execute(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'search_vector_embeddings'"
                )
            )
            vector_sql = vector_sql_result.scalar()
            expected_dimension_sql = f"float[{self._vector_dimensions}]"

            if vector_sql and expected_dimension_sql not in vector_sql:
                logger.warning(
                    f"Embedding dimension mismatch (expected {self._vector_dimensions}), "
                    "recreating search_vector_embeddings"
                )
                await session.execute(text("DROP TABLE IF EXISTS search_vector_embeddings"))

            await session.execute(create_sqlite_search_vector_embeddings(self._vector_dimensions))
            await session.commit()

        logger.info(f"SQLite vector tables ready (dimensions={self._vector_dimensions})")
        self._vector_tables_initialized = True

    async def _prepare_vector_session(self, session: AsyncSession) -> None:
        """Load sqlite-vec extension for the session."""
        await self._ensure_sqlite_vec_loaded(session)

    async def _run_vector_query(
        self,
        session: AsyncSession,
        query_embedding: list[float],
        candidate_limit: int,
    ) -> list[dict]:
        query_embedding_json = json.dumps(query_embedding)
        vector_result = await session.execute(
            text(
                "WITH vector_matches AS ("
                "  SELECT rowid, distance "
                "  FROM search_vector_embeddings "
                "  WHERE embedding MATCH :query_embedding "
                "    AND k = :vector_k"
                ") "
                "SELECT c.entity_id, c.chunk_key, vector_matches.distance AS best_distance "
                "FROM vector_matches "
                "JOIN search_vector_chunks c ON c.id = vector_matches.rowid "
                "WHERE c.project_id = :project_id "
                "ORDER BY best_distance ASC "
                "LIMIT :vector_k"
            ),
            {
                "query_embedding": query_embedding_json,
                "project_id": self.project_id,
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
        rowids = [row_id for row_id, _ in jobs]
        delete_params = {f"rowid_{idx}": rowid for idx, rowid in enumerate(rowids)}
        delete_placeholders = ", ".join(f":rowid_{idx}" for idx in range(len(rowids)))
        await session.execute(
            text(f"DELETE FROM search_vector_embeddings WHERE rowid IN ({delete_placeholders})"),
            delete_params,
        )

        insert_rows = [
            {"rowid": row_id, "embedding": json.dumps(embedding)}
            for (row_id, _), embedding in zip(jobs, embeddings, strict=True)
        ]
        await session.execute(
            text(
                "INSERT INTO search_vector_embeddings (rowid, embedding) "
                "VALUES (:rowid, :embedding)"
            ),
            insert_rows,
        )

    async def _delete_entity_chunks(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> None:
        # sqlite-vec has no CASCADE — must delete embeddings before chunks
        await session.execute(
            text(
                "DELETE FROM search_vector_embeddings "
                "WHERE rowid IN ("
                "SELECT id FROM search_vector_chunks "
                "WHERE project_id = :project_id AND entity_id = :entity_id"
                ")"
            ),
            {"project_id": self.project_id, "entity_id": entity_id},
        )
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
        stale_params = {
            "project_id": self.project_id,
            "entity_id": entity_id,
            **{f"row_{idx}": row_id for idx, row_id in enumerate(stale_ids)},
        }
        stale_placeholders = ", ".join(f":row_{idx}" for idx in range(len(stale_ids)))
        await session.execute(
            text(f"DELETE FROM search_vector_embeddings WHERE rowid IN ({stale_placeholders})"),
            stale_params,
        )
        await session.execute(
            text(
                "DELETE FROM search_vector_chunks "
                f"WHERE id IN ({stale_placeholders}) "
                "AND project_id = :project_id AND entity_id = :entity_id"
            ),
            stale_params,
        )

    async def _update_timestamp_sql(self) -> str:
        return "CURRENT_TIMESTAMP"  # pragma: no cover

    def _distance_to_similarity(self, distance: float) -> float:
        """Convert L2 distance to cosine similarity for normalized embeddings.

        sqlite-vec vec0 returns Euclidean (L2) distance by default.
        For unit-normalized vectors: L2² = 2·(1 - cos_sim), so cos_sim = 1 - L2²/2.
        """
        return max(0.0, 1.0 - (distance * distance) / 2.0)

    def _orphan_detection_sql(self) -> str:
        """SQLite sqlite-vec uses rowid-based embedding table."""
        return (
            "SELECT c.id FROM search_vector_chunks c "
            "LEFT JOIN search_vector_embeddings e ON e.rowid = c.id "
            "WHERE c.project_id = :project_id AND c.entity_id = :entity_id "
            "AND e.rowid IS NULL"
        )

    # ------------------------------------------------------------------
    # Index / bulk index overrides (FTS-only, no vector side-effects)
    # ------------------------------------------------------------------

    async def index_item(self, search_index_row: SearchIndexRow) -> None:
        """Index a single row in FTS only.

        Vector chunks are derived asynchronously via sync_entity_vectors().
        """
        await super().index_item(search_index_row)

    async def bulk_index_items(self, search_index_rows: List[SearchIndexRow]) -> None:
        """Index multiple rows in FTS only."""
        await super().bulk_index_items(search_index_rows)

    # ------------------------------------------------------------------
    # FTS search (backend-specific)
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
        """Search across all indexed content using SQLite FTS5."""
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

        # --- FTS mode (SQLite-specific) ---
        conditions = []
        match_conditions = []
        params = {}
        order_by_clause = ""
        from_clause = "search_index"

        # Handle text search for title and content
        if search_text:
            # Skip FTS for wildcard-only queries that would cause "unknown special query" errors
            if search_text.strip() == "*" or search_text.strip() == "":
                # For wildcard searches, don't add any text conditions - return all results
                pass
            else:
                # Use _prepare_search_term to handle both Boolean and non-Boolean queries
                processed_text = self._prepare_search_term(search_text.strip())
                params["text"] = processed_text
                match_conditions.append(
                    "(search_index.title MATCH :text OR search_index.content_stems MATCH :text)"
                )

        # Handle title match search
        if title:
            title_text = self._prepare_search_term(title.strip(), is_prefix=False)
            params["title_text"] = title_text
            match_conditions.append("search_index.title MATCH :title_text")

        # Handle permalink exact search
        if permalink:
            params["permalink"] = permalink
            conditions.append("search_index.permalink = :permalink")

        # Handle permalink match search, supports *
        if permalink_match:
            # For GLOB patterns, don't use _prepare_search_term as it will quote slashes
            # GLOB patterns need to preserve their syntax
            permalink_text = permalink_match.lower().strip()
            params["permalink"] = permalink_text
            if "*" in permalink_match:
                conditions.append("search_index.permalink GLOB :permalink")
            else:
                # For exact matches without *, we can use FTS5 MATCH
                # but only prepare the term if it doesn't look like a path
                if "/" in permalink_text:
                    conditions.append("search_index.permalink = :permalink")
                else:
                    permalink_text = self._prepare_search_term(permalink_text, is_prefix=False)
                    params["permalink"] = permalink_text
                    match_conditions.append("search_index.permalink MATCH :permalink")

        # Handle entity type filter
        if search_item_types:
            type_list = ", ".join(f"'{t.value}'" for t in search_item_types)
            conditions.append(f"search_index.type IN ({type_list})")

        # Handle note type filter (frontmatter type field)
        if note_types:
            type_list = ", ".join(f"'{t}'" for t in note_types)
            conditions.append(
                f"json_extract(search_index.metadata, '$.note_type') IN ({type_list})"
            )

        # Handle date filter using datetime() for proper comparison
        if after_date:
            params["after_date"] = after_date
            conditions.append("datetime(search_index.created_at) > datetime(:after_date)")

            # order by most recent first
            order_by_clause = ", search_index.updated_at DESC"

        # Handle structured metadata filters (frontmatter)
        if metadata_filters:
            parsed_filters = parse_metadata_filters(metadata_filters)
            from_clause = "search_index JOIN entity ON search_index.entity_id = entity.id"
            entity_columns = await self._get_entity_columns()

            for idx, filt in enumerate(parsed_filters):
                path_param = f"meta_path_{idx}"
                extract_expr = None
                use_tags_column = False

                if filt.path_parts == ["status"] and "frontmatter_status" in entity_columns:
                    extract_expr = "entity.frontmatter_status"
                elif filt.path_parts == ["type"] and "frontmatter_type" in entity_columns:
                    extract_expr = "entity.frontmatter_type"
                elif filt.path_parts == ["tags"] and "tags_json" in entity_columns:
                    extract_expr = "entity.tags_json"
                    use_tags_column = True

                if extract_expr is None:
                    params[path_param] = build_sqlite_json_path(filt.path_parts)
                    extract_expr = f"json_extract(entity.entity_metadata, :{path_param})"

                if filt.op == "eq":
                    value_param = f"meta_val_{idx}"
                    params[value_param] = filt.value
                    conditions.append(f"{extract_expr} = :{value_param}")
                    continue

                if filt.op == "in":
                    placeholders = []
                    for j, val in enumerate(filt.value):
                        value_param = f"meta_val_{idx}_{j}"
                        params[value_param] = val
                        placeholders.append(f":{value_param}")
                    conditions.append(f"{extract_expr} IN ({', '.join(placeholders)})")
                    continue

                if filt.op == "contains":
                    tag_conditions = []
                    for j, val in enumerate(filt.value):
                        value_param = f"meta_val_{idx}_{j}"
                        params[value_param] = val
                        like_param = f"{value_param}_like"
                        params[like_param] = f'%"{val}"%'
                        like_param_single = f"{value_param}_like_single"
                        params[like_param_single] = f"%'{val}'%"
                        json_each_expr = (
                            "json_each(entity.tags_json)"
                            if use_tags_column
                            else f"json_each(entity.entity_metadata, :{path_param})"
                        )
                        tag_conditions.append(
                            "("
                            f"EXISTS (SELECT 1 FROM {json_each_expr} WHERE value = :{value_param}) "
                            f"OR {extract_expr} LIKE :{like_param} "
                            f"OR {extract_expr} LIKE :{like_param_single}"
                            ")"
                        )
                    conditions.append(" AND ".join(tag_conditions))
                    continue

                if filt.op in {"gt", "gte", "lt", "lte", "between"}:
                    compare_expr = (
                        f"CAST({extract_expr} AS REAL)"
                        if filt.comparison == "numeric"
                        else extract_expr
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

        # Trigger: SQLite FTS MATCH predicates combined with JOINs can fail with
        # "unable to use function MATCH in the requested context".
        # Why: MATCH needs to run in an FTS-valid context.
        # Outcome: evaluate MATCH clauses in an FTS subquery and filter outer rows by rowid.
        if metadata_filters and match_conditions:
            match_where = " AND ".join(match_conditions)
            conditions.append(
                f"search_index.rowid IN (SELECT rowid FROM search_index WHERE {match_where})"
            )
        else:
            conditions.extend(match_conditions)

        # Always filter by project_id
        params["project_id"] = self.project_id
        conditions.append("search_index.project_id = :project_id")

        # set limit on search query
        params["limit"] = limit
        params["offset"] = offset

        # Build WHERE clause
        where_clause = " AND ".join(conditions) if conditions else "1=1"

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
                bm25(search_index) as score
            FROM {from_clause}
            WHERE {where_clause}
            ORDER BY score ASC {order_by_clause}
            LIMIT :limit
            OFFSET :offset
        """

        logger.trace(f"Search {sql} params: {params}")
        try:
            async with db.scoped_session(self.session_maker) as session:
                result = await session.execute(text(sql), params)
                rows = result.fetchall()
        except Exception as e:
            # Handle FTS5 syntax errors and provide user-friendly feedback
            if "fts5: syntax error" in str(e).lower():  # pragma: no cover
                logger.warning(f"FTS5 syntax error for search term: {search_text}, error: {e}")
                # Return empty results rather than crashing
                return []
            else:
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
                score=row.score,
                metadata=json.loads(row.metadata) if row.metadata else {},
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
