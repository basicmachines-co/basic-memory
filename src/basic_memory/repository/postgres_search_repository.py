"""PostgreSQL tsvector-based search repository implementation."""

import asyncio
import json
import re
from datetime import datetime
from typing import List, Optional

import logfire
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, ConfigManager, DatabaseBackend
from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.embedding_provider_factory import create_embedding_provider
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_query import relaxed_query_words
from basic_memory.repository.semantic_chunking import VectorChunkRecord
from basic_memory.repository.search_repository_base import (
    SearchRepositoryBase,
    VectorChunkState,
)
from basic_memory.repository.metadata_filters import parse_metadata_filters
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError
from basic_memory.repository.semantic_vector_index import SemanticVectorIndex
from basic_memory.repository.semantic_vector_index_factory import (
    build_vector_index_scope,
    resolve_semantic_vector_index_name,
)
from basic_memory.repository.pgvector_index import PgVectorIndex
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
        vector_index_name: str | None = None,
        vector_index: SemanticVectorIndex | None = None,
    ):
        super().__init__(session_maker, project_id)
        self._app_config = app_config or ConfigManager().config
        self._semantic_enabled = self._app_config.semantic_search_enabled
        self._semantic_vector_k = self._app_config.semantic_vector_k
        self._semantic_min_similarity = self._app_config.semantic_min_similarity
        self._semantic_embedding_sync_batch_size = (
            self._app_config.semantic_embedding_sync_batch_size
        )
        self._semantic_postgres_prepare_concurrency = (
            self._app_config.semantic_postgres_prepare_concurrency
        )
        self._embedding_provider = embedding_provider
        self._semantic_vector_index_name = vector_index_name or "pgvector"
        self._vector_dimensions = 384
        self._vector_tables_initialized = False
        self._vector_tables_lock = asyncio.Lock()

        if self._semantic_enabled and self._embedding_provider is None:
            self._embedding_provider = create_embedding_provider(self._app_config)
        if self._embedding_provider is not None:
            self._vector_dimensions = self._embedding_provider.dimensions
            effective_name = vector_index_name or resolve_semantic_vector_index_name(
                self._app_config,
                DatabaseBackend.POSTGRES,
            )
            if vector_index is None:
                if effective_name != "pgvector":
                    raise SemanticDependenciesMissingError(
                        f"Semantic vector index '{effective_name}' must be created by the "
                        "search repository composition root."
                    )
                vector_index = PgVectorIndex(
                    session_maker,
                    build_vector_index_scope(
                        self._app_config,
                        self._embedding_provider,
                        project_id,
                    ),
                )
            self._semantic_vector_index_name = effective_name
            self._semantic_vector_index = vector_index

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

    @staticmethod
    def _relaxed_tsquery_text(search_text: Optional[str]) -> Optional[str]:
        """OR-relaxed tsquery expression for a failed strict query, or None."""
        words = relaxed_query_words(search_text)
        if not words:
            return None
        return " | ".join(f"{word}:*" for word in words)

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
            # Strip sentence punctuation from word edges so question-form
            # queries produce clean lexemes (parity with SQLite FTS5 prep).
            # The tsquery tokenizer ignores this punctuation anyway; leaving it
            # in only risks tsquery syntax errors. Interior characters are kept.
            words = [w.strip("?!.,;") for w in cleaned_term.split()]
            words = [w for w in words if w]
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

        # Single word: strip edge punctuation; guard the now-empty case so a
        # bare ":*"/"" never reaches tsquery.
        cleaned_term = cleaned_term.strip().strip("?!.,;")
        if not cleaned_term:
            return "NOSPECIALCHARS:*"
        if is_prefix:
            return f"{cleaned_term}:*"
        else:
            return cleaned_term

    # ------------------------------------------------------------------
    # Abstract hook implementations (vector/semantic, Postgres-specific)
    # ------------------------------------------------------------------

    async def _ensure_vector_tables(self) -> None:
        self._assert_semantic_available()
        if not hasattr(self, "_semantic_vector_index"):
            assert self._embedding_provider is not None
            self._semantic_vector_index_name = "pgvector"
            self._semantic_vector_index = PgVectorIndex(
                self.session_maker,
                build_vector_index_scope(
                    self._app_config,
                    self._embedding_provider,
                    self.project_id,
                ),
            )
        if self._vector_tables_initialized:
            return

        logger.debug("Ensuring Postgres vector tables exist for semantic search")

        async with self._vector_tables_lock:
            if self._vector_tables_initialized:
                return

            async with db.scoped_session(self.session_maker) as session:
                # --- Chunks table (dimension-independent, may already exist via migration) ---
                # Trigger: fresh Postgres projects may not have vector chunk tables yet.
                # Why: runtime can bootstrap missing tables, but schema evolution must stay
                # in Alembic to avoid concurrent ALTER TABLE deadlocks during indexing.
                # Outcome: new installs create the current schema; upgrades rely on migration.
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
                            entity_fingerprint TEXT NOT NULL,
                            embedding_model TEXT NOT NULL,
                            vector_index TEXT NOT NULL,
                            embedding_status TEXT NOT NULL
                                CHECK (embedding_status IN ('pending', 'ready')),
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

                await session.commit()

            await self._semantic_vector_index.initialize()

            logger.debug(f"Postgres vector tables ready (dimensions={self._vector_dimensions})")
            self._vector_tables_initialized = True

    async def _run_vector_query(
        self,
        session: AsyncSession,
        query_embedding: list[float],
        candidate_limit: int,
    ) -> list[dict]:
        return await super()._run_vector_query(session, query_embedding, candidate_limit)

    def _vector_prepare_window_size(self) -> int:
        """Use a bounded config-driven prepare window for Postgres vector sync."""
        return self._semantic_postgres_prepare_concurrency

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
        """Use Postgres UPSERT to rewrite only the scheduled chunk rows."""
        if not scheduled_records:
            return []

        self._assert_manifest_vector_ownership(
            current.vector_index
            for record in scheduled_records
            if (current := existing_by_key.get(record["chunk_key"])) is not None
        )
        upsert_params: dict[str, object] = {
            "project_id": self.project_id,
            "entity_id": entity_id,
            "vector_index": self._semantic_vector_index_name,
        }
        upsert_values: list[str] = []
        # The SQL template is built from integer enumerate() indices only.
        # No user-controlled text is interpolated into the statement.
        for index, record in enumerate(scheduled_records):
            upsert_params[f"chunk_key_{index}"] = record["chunk_key"]
            upsert_params[f"chunk_text_{index}"] = record["chunk_text"]
            upsert_params[f"source_hash_{index}"] = record["source_hash"]
            upsert_params[f"entity_fingerprint_{index}"] = entity_fingerprint
            upsert_params[f"embedding_model_{index}"] = embedding_model
            upsert_values.append(
                "("
                ":entity_id, :project_id, "
                f":chunk_key_{index}, :chunk_text_{index}, :source_hash_{index}, "
                f":entity_fingerprint_{index}, :embedding_model_{index}, "
                ":vector_index, 'pending', NOW()"
                ")"
            )

        upsert_result = await session.execute(
            text(f"""
                INSERT INTO search_vector_chunks (
                    entity_id,
                    project_id,
                    chunk_key,
                    chunk_text,
                    source_hash,
                    entity_fingerprint,
                    embedding_model,
                    vector_index,
                    embedding_status,
                    updated_at
                ) VALUES {", ".join(upsert_values)}
                ON CONFLICT (project_id, entity_id, chunk_key) DO UPDATE SET
                    chunk_text = EXCLUDED.chunk_text,
                    source_hash = EXCLUDED.source_hash,
                    entity_fingerprint = EXCLUDED.entity_fingerprint,
                    embedding_model = EXCLUDED.embedding_model,
                    vector_index = EXCLUDED.vector_index,
                    embedding_status = EXCLUDED.embedding_status,
                    updated_at = NOW()
                RETURNING id, chunk_key
            """),
            upsert_params,
        )
        upserted_ids_by_key = {
            str(row["chunk_key"]): int(row["id"]) for row in upsert_result.mappings().all()
        }
        return [
            (upserted_ids_by_key[record["chunk_key"]], record["chunk_text"])
            for record in scheduled_records
        ]

    async def _delete_entity_chunks(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> None:
        await super()._delete_entity_chunks(session, entity_id)

    async def _delete_stale_chunks(
        self,
        session: AsyncSession,
        stale_ids: list[int],
        entity_id: int,
    ) -> None:
        await super()._delete_stale_chunks(session, stale_ids, entity_id)

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

    @staticmethod
    def _is_tsquery_syntax_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return (
            "syntax error in tsquery" in msg
            or "invalid input syntax for type tsquery" in msg
            or "no operand in tsquery" in msg
            or "no operator in tsquery" in msg
        )

    async def _build_fts_query_parts(
        self,
        search_text: Optional[str] = None,
        permalink: Optional[str] = None,
        permalink_match: Optional[str] = None,
        title: Optional[str] = None,
        note_types: Optional[List[str]] = None,
        after_date: Optional[datetime] = None,
        search_item_types: Optional[List[SearchItemType]] = None,
        categories: Optional[List[str]] = None,
        metadata_filters: Optional[dict] = None,
    ) -> tuple[str, str, dict, str, str]:
        """Build Postgres FTS FROM/WHERE params shared by search and count."""
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

        # Handle search item type filter (parameterized for defense-in-depth)
        if search_item_types:
            type_placeholders = []
            for idx, t in enumerate(search_item_types):
                param_name = f"search_type_{idx}"
                params[param_name] = t.value
                type_placeholders.append(f":{param_name}")
            conditions.append(f"search_index.type IN ({', '.join(type_placeholders)})")

        # Handle observation category filter (parameterized for defense-in-depth).
        # Trigger: caller passed `categories` to scope observation results.
        # Why: `entity_types=["observation"]` only narrows to the observation row type;
        #      callers expect exact-category matching, not incidental text matches.
        # Outcome: only rows whose indexed category exactly equals a requested value
        #          survive (entities/relations have NULL category and are excluded).
        if categories:
            category_placeholders = []
            for idx, category in enumerate(categories):
                param_name = f"category_{idx}"
                params[param_name] = category
                category_placeholders.append(f":{param_name}")
            conditions.append(f"search_index.category IN ({', '.join(category_placeholders)})")

        # Handle note type filter (frontmatter type field, parameterized).
        # Trigger: caller passed `note_types` to scope by the frontmatter `type` field.
        # Why: the stored note_type preserves the frontmatter casing (e.g. `Chapter`),
        #      but the filter is documented case-insensitive. JSONB `@>` containment is
        #      exact-match, so capitalized types were unfindable.
        # Outcome: compare LOWER(metadata->>'note_type') against lowercased filter
        #          values so `note_types=["Chapter"]` matches a stored `Chapter`.
        if note_types:
            type_placeholders = []
            for idx, note_type in enumerate(note_types):
                param_name = f"note_type_{idx}"
                params[param_name] = note_type.lower()
                type_placeholders.append(f":{param_name}")
            conditions.append(
                f"LOWER(search_index.metadata->>'note_type') IN ({', '.join(type_placeholders)})"
            )

        # Handle date filter
        if after_date:
            params["after_date"] = after_date
            # Filter on updated_at so recently-edited notes are included even when created_at is old
            conditions.append("search_index.updated_at > :after_date")
            # order by most recent first
            order_by_clause = ", search_index.updated_at DESC"

        # Handle structured metadata filters (frontmatter)
        # Uses jsonb_extract_path_text() / jsonb_extract_path() with parameterized
        # path parts instead of #>> / #> with interpolated paths.
        if metadata_filters:
            parsed_filters = parse_metadata_filters(metadata_filters)
            from_clause = "search_index JOIN entity ON search_index.entity_id = entity.id"
            metadata_expr = "entity.entity_metadata::jsonb"

            for idx, filt in enumerate(parsed_filters):
                # Parameterize each JSON path part individually
                path_param_names = []
                for j, part in enumerate(filt.path_parts):
                    path_param = f"meta_path_{idx}_{j}"
                    params[path_param] = part
                    path_param_names.append(f":{path_param}")
                path_args = ", ".join(path_param_names)
                text_expr = f"jsonb_extract_path_text({metadata_expr}, {path_args})"
                json_expr = f"jsonb_extract_path({metadata_expr}, {path_args})"

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
                    base_param = f"meta_val_{idx}"
                    tag_conditions = []
                    # Require all values to be present
                    for j, val in enumerate(filt.value):
                        tag_param = f"{base_param}_{j}"
                        params[tag_param] = json.dumps([val])
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
                        f"{text_expr}::double precision"
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

        return from_clause, where_clause, params, order_by_clause, score_expr

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
        metadata_filters: Optional[dict] = None,
        retrieval_mode: SearchRetrievalMode = SearchRetrievalMode.FTS,
        min_similarity: Optional[float] = None,
        limit: int = 10,
        offset: int = 0,
        allow_relaxed: bool = False,
        session: AsyncSession | None = None,
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
            categories=categories,
            metadata_filters=metadata_filters,
            retrieval_mode=retrieval_mode,
            min_similarity=min_similarity,
            limit=limit,
            offset=offset,
        )
        if dispatched is not None:
            return dispatched

        # --- FTS mode (Postgres-specific) ---
        (
            from_clause,
            where_clause,
            params,
            order_by_clause,
            score_expr,
        ) = await self._build_fts_query_parts(
            search_text=search_text,
            permalink=permalink,
            permalink_match=permalink_match,
            title=title,
            note_types=note_types,
            after_date=after_date,
            search_item_types=search_item_types,
            categories=categories,
            metadata_filters=metadata_filters,
        )

        # set limit and offset
        params["limit"] = limit
        params["offset"] = offset

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
            ORDER BY score DESC {order_by_clause}, search_index.id ASC
            LIMIT :limit
            OFFSET :offset
        """

        logger.trace(f"Search {sql} params: {params}")

        use_savepoint = session is not None or allow_relaxed

        async def execute_rows(active_session: AsyncSession, query_params: dict):
            # PostgreSQL leaves a transaction unusable after invalid tsquery syntax.
            # Scope retryable or caller-owned attempts to a savepoint so a relaxed
            # retry—and any caller continuing to use its session—starts healthy.
            if use_savepoint:
                async with active_session.begin_nested():
                    result = await active_session.execute(text(sql), query_params)
                    return result.fetchall()
            result = await active_session.execute(text(sql), query_params)
            return result.fetchall()

        async def run_search(active_session: AsyncSession):
            relaxed = self._relaxed_tsquery_text(search_text) if allow_relaxed else None
            strict_syntax_error = False
            try:
                rows = await execute_rows(active_session, params)
            except Exception as exc:
                if not (self._is_tsquery_syntax_error(exc) and relaxed and params.get("text")):
                    raise
                strict_syntax_error = True
                rows = []

            # Trigger: multi-word natural-language query matched nothing
            # under the default all-terms-AND tsquery semantics, or its punctuation
            # produced invalid strict tsquery syntax.
            # Why: questions rarely have every word in one document;
            # without relaxation the FTS half of hybrid search contributes zero
            # candidates. The relaxed renderer also tokenizes punctuation safely.
            # Outcome: one retry with OR-joined prefix lexemes; ts_rank
            # still ranks multi-term matches first.
            if relaxed and not rows and params.get("text"):
                retry_reason = "invalid syntax" if strict_syntax_error else "0 results"
                logger.debug(
                    f"Strict Postgres FTS returned {retry_reason}; retrying relaxed FTS query "
                    f"strict='{search_text}' relaxed='{relaxed}'"
                )
                with logfire.span(
                    "search.relaxed_fts_retry",
                    backend="postgres",
                    reason="syntax_error" if strict_syntax_error else "empty_result",
                    token_count=len(relaxed_query_words(search_text) or ()),
                    limit=limit,
                    offset=offset,
                ):
                    rows = await execute_rows(
                        active_session,
                        {**params, "text": relaxed},
                    )
            return rows

        try:
            if session is not None:
                rows = await run_search(session)
            else:
                async with db.scoped_session(self.session_maker) as owned_session:
                    rows = await run_search(owned_session)
        except Exception as e:
            if self._is_tsquery_syntax_error(e):
                logger.warning(f"tsquery syntax error for search term: {search_text}, error: {e}")
                return []

            # Re-raise other database errors
            logger.error(f"Database error during search: {e}")
            raise

        results = [SearchIndexRow.from_mapping(row._asdict()) for row in rows]

        logger.trace(f"Found {len(results)} search results")
        for r in results:
            logger.trace(
                f"Search result: project_id: {r.project_id} type:{r.type} title: {r.title} permalink: {r.permalink} score: {r.score}"
            )

        return results

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
        metadata_filters: Optional[dict] = None,
        retrieval_mode: SearchRetrievalMode = SearchRetrievalMode.FTS,
        min_similarity: Optional[float] = None,
        allow_relaxed: bool = False,
    ) -> int:
        """Count indexed content matching the Postgres FTS query."""
        if retrieval_mode != SearchRetrievalMode.FTS:
            return await super().count(
                search_text=search_text,
                permalink=permalink,
                permalink_match=permalink_match,
                title=title,
                note_types=note_types,
                after_date=after_date,
                search_item_types=search_item_types,
                categories=categories,
                metadata_filters=metadata_filters,
                retrieval_mode=retrieval_mode,
                min_similarity=min_similarity,
            )

        (
            from_clause,
            where_clause,
            params,
            _order_by_clause,
            _score_expr,
        ) = await self._build_fts_query_parts(
            search_text=search_text,
            permalink=permalink,
            permalink_match=permalink_match,
            title=title,
            note_types=note_types,
            after_date=after_date,
            search_item_types=search_item_types,
            categories=categories,
            metadata_filters=metadata_filters,
        )
        sql = f"SELECT COUNT(*) FROM {from_clause} WHERE {where_clause}"
        logger.trace(f"Count {sql} params: {params}")

        async def execute_count(active_session: AsyncSession, query_params: dict) -> int:
            if allow_relaxed:
                async with active_session.begin_nested():
                    result = await active_session.execute(text(sql), query_params)
                    return int(result.scalar_one())
            result = await active_session.execute(text(sql), query_params)
            return int(result.scalar_one())

        try:
            async with db.scoped_session(self.session_maker) as session:
                relaxed = self._relaxed_tsquery_text(search_text) if allow_relaxed else None
                strict_syntax_error = False
                try:
                    total = await execute_count(session, params)
                except Exception as exc:
                    if not (self._is_tsquery_syntax_error(exc) and relaxed and params.get("text")):
                        raise
                    strict_syntax_error = True
                    total = 0

                if relaxed and total == 0 and params.get("text"):
                    with logfire.span(
                        "search.count.relaxed_fts_retry",
                        backend="postgres",
                        reason="syntax_error" if strict_syntax_error else "empty_result",
                        token_count=len(relaxed_query_words(search_text) or ()),
                    ):
                        total = await execute_count(
                            session,
                            {**params, "text": relaxed},
                        )
                return total
        except Exception as e:
            if self._is_tsquery_syntax_error(e):
                logger.warning(f"tsquery syntax error for search term: {search_text}, error: {e}")
                return 0
            logger.error(f"Database error during search count: {e}")
            raise
