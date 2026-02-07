"""SQLite FTS5-based search repository implementation."""

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
from basic_memory.models.search import (
    CREATE_SEARCH_INDEX,
    CREATE_SQLITE_SEARCH_VECTOR_CHUNKS,
    CREATE_SQLITE_SEARCH_VECTOR_CHUNKS_PROJECT_ENTITY,
    CREATE_SQLITE_SEARCH_VECTOR_CHUNKS_UNIQUE,
    create_sqlite_search_vector_embeddings,
)
from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.fastembed_provider import FastEmbedEmbeddingProvider
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_repository_base import SearchRepositoryBase
from basic_memory.repository.metadata_filters import parse_metadata_filters, build_sqlite_json_path
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
        self._embedding_provider = embedding_provider
        self._sqlite_vec_lock = asyncio.Lock()
        self._vector_tables_initialized = False
        self._vector_dimensions = 384

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

    async def _get_entity_columns(self) -> set[str]:
        if self._entity_columns is None:
            async with db.scoped_session(self.session_maker) as session:
                result = await session.execute(text("PRAGMA table_info(entity)"))
                self._entity_columns = {row[1] for row in result.fetchall()}
        return self._entity_columns

    async def init_search_index(self):
        """Create FTS5 virtual table for search if it doesn't exist.

        Uses CREATE VIRTUAL TABLE IF NOT EXISTS to preserve existing indexed data
        across server restarts.
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

    async def _ensure_sqlite_vec_loaded(self, session) -> None:
        try:
            await session.execute(text("SELECT vec_version()"))
            return
        except Exception:
            pass

        try:
            import sqlite_vec
        except ImportError as exc:
            raise SemanticDependenciesMissingError(
                "Semantic search dependencies are missing. "
                "Install with: pip install -e '.[semantic]'"
            ) from exc

        async with self._sqlite_vec_lock:
            try:
                await session.execute(text("SELECT vec_version()"))
                return
            except Exception:
                pass

            async_connection = await session.connection()
            raw_connection = await async_connection.get_raw_connection()
            driver_connection = raw_connection.driver_connection
            await driver_connection.enable_load_extension(True)
            await driver_connection.load_extension(sqlite_vec.loadable_path())
            await driver_connection.enable_load_extension(False)
            await session.execute(text("SELECT vec_version()"))

    async def _ensure_vector_tables(self) -> None:
        self._assert_semantic_available()
        if self._vector_tables_initialized:
            return

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
                await session.execute(text("DROP TABLE IF EXISTS search_vector_embeddings"))

            await session.execute(create_sqlite_search_vector_embeddings(self._vector_dimensions))
            await session.commit()

        self._vector_tables_initialized = True

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

    async def index_item(self, search_index_row: SearchIndexRow) -> None:
        """Index a single row in FTS only.

        Vector chunks are derived asynchronously via sync_entity_vectors().
        """
        await super().index_item(search_index_row)

    async def bulk_index_items(self, search_index_rows: List[SearchIndexRow]) -> None:
        """Index multiple rows in FTS only."""
        await super().bulk_index_items(search_index_rows)

    async def sync_entity_vectors(self, entity_id: int) -> None:
        """Sync semantic chunk rows + sqlite-vec embeddings for a single entity."""
        self._assert_semantic_available()
        await self._ensure_vector_tables()
        assert self._embedding_provider is not None

        async with db.scoped_session(self.session_maker) as session:
            await self._ensure_sqlite_vec_loaded(session)
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
                await session.commit()
                return

            chunk_records = self._build_chunk_records(rows)
            if not chunk_records:
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
            stale_rows = [
                row
                for chunk_key, row in existing_by_key.items()
                if chunk_key not in incoming_hashes
            ]

            if stale_rows:
                stale_params = {
                    "project_id": self.project_id,
                    "entity_id": entity_id,
                    **{f"row_{idx}": row.id for idx, row in enumerate(stale_rows)},
                }
                stale_placeholders = ", ".join(f":row_{idx}" for idx in range(len(stale_rows)))
                await session.execute(
                    text(
                        "DELETE FROM search_vector_embeddings "
                        f"WHERE rowid IN ({stale_placeholders})"
                    ),
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
                            "updated_at = CURRENT_TIMESTAMP "
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
                        ":entity_id, :project_id, :chunk_key, :chunk_text, :source_hash, "
                        "CURRENT_TIMESTAMP"
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

        texts = [text_value for _, text_value in embedding_jobs]
        embeddings = await self._embedding_provider.embed_documents(texts)
        if len(embeddings) != len(embedding_jobs):
            raise RuntimeError("Embedding provider returned an unexpected number of vectors.")

        async with db.scoped_session(self.session_maker) as session:
            await self._ensure_sqlite_vec_loaded(session)
            rowids = [row_id for row_id, _ in embedding_jobs]
            delete_params = {
                **{f"rowid_{idx}": rowid for idx, rowid in enumerate(rowids)},
            }
            delete_placeholders = ", ".join(f":rowid_{idx}" for idx in range(len(rowids)))
            await session.execute(
                text(
                    f"DELETE FROM search_vector_embeddings WHERE rowid IN ({delete_placeholders})"
                ),
                delete_params,
            )

            insert_rows = [
                {"rowid": row_id, "embedding": json.dumps(embedding)}
                for (row_id, _), embedding in zip(embedding_jobs, embeddings, strict=True)
            ]
            await session.execute(
                text(
                    "INSERT INTO search_vector_embeddings (rowid, embedding) "
                    "VALUES (:rowid, :embedding)"
                ),
                insert_rows,
            )
            await session.commit()

    def _split_fixed_windows(self, text_value: str) -> list[str]:
        if len(text_value) <= MAX_VECTOR_CHUNK_CHARS:
            return [text_value] if text_value else []

        chunks: list[str] = []
        start = 0
        while start < len(text_value):
            end = min(start + MAX_VECTOR_CHUNK_CHARS, len(text_value))
            chunk = text_value[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(text_value):
                break
            next_start = end - VECTOR_CHUNK_OVERLAP_CHARS
            start = next_start if next_start > start else end
        return chunks

    def _split_text_into_chunks(self, text_value: str) -> list[str]:
        normalized_text = text_value.strip()
        if not normalized_text:
            return []
        if len(normalized_text) <= MAX_VECTOR_CHUNK_CHARS:
            return [normalized_text]

        sections: list[str] = []
        current_lines: list[str] = []
        for line in normalized_text.splitlines():
            if HEADER_LINE_PATTERN.match(line) and current_lines:
                sections.append("\n".join(current_lines).strip())
                current_lines = [line]
                continue
            current_lines.append(line)
        if current_lines:
            sections.append("\n".join(current_lines).strip())

        chunked_sections: list[str] = []
        for section in sections:
            if len(section) <= MAX_VECTOR_CHUNK_CHARS:
                chunked_sections.append(section)
                continue

            paragraphs = [part.strip() for part in section.split("\n\n") if part.strip()]
            if not paragraphs:
                chunked_sections.extend(self._split_fixed_windows(section))
                continue

            current_chunk = ""
            for paragraph in paragraphs:
                candidate = paragraph if not current_chunk else f"{current_chunk}\n\n{paragraph}"
                if len(candidate) <= MAX_VECTOR_CHUNK_CHARS:
                    current_chunk = candidate
                    continue

                if current_chunk:
                    chunked_sections.append(current_chunk)

                if len(paragraph) <= MAX_VECTOR_CHUNK_CHARS:
                    current_chunk = paragraph
                    continue

                long_chunks = self._split_fixed_windows(paragraph)
                if not long_chunks:
                    current_chunk = ""
                    continue
                chunked_sections.extend(long_chunks[:-1])
                current_chunk = long_chunks[-1]

            if current_chunk:
                chunked_sections.append(current_chunk)

        return [chunk for chunk in chunked_sections if chunk.strip()]

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
        """Run vector-only search over entity vectors with optional filters."""
        self._assert_semantic_available()
        await self._ensure_vector_tables()
        assert self._embedding_provider is not None
        query_embedding = await self._embedding_provider.embed_query(search_text.strip())
        query_embedding_json = json.dumps(query_embedding)
        candidate_limit = max(self._semantic_vector_k, (limit + offset) * 5)

        async with db.scoped_session(self.session_maker) as session:
            await self._ensure_sqlite_vec_loaded(session)
            vector_result = await session.execute(
                text(
                    "WITH vector_matches AS ("
                    "  SELECT rowid, distance "
                    "  FROM search_vector_embeddings "
                    "  WHERE embedding MATCH :query_embedding "
                    "    AND k = :vector_k"
                    ") "
                    "SELECT c.entity_id, MIN(vector_matches.distance) AS best_distance "
                    "FROM vector_matches "
                    "JOIN search_vector_chunks c ON c.id = vector_matches.rowid "
                    "WHERE c.project_id = :project_id "
                    "GROUP BY c.entity_id "
                    "ORDER BY best_distance ASC "
                    "LIMIT :vector_k"
                ),
                {
                    "query_embedding": query_embedding_json,
                    "project_id": self.project_id,
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

        # Trigger: user supplied non-text filters.
        # Why: reuse existing SQL filter semantics (metadata/date/type) for correctness.
        # Outcome: vector scoring only applies to entities that already pass filters.
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
                placeholders = ",".join(f":id_{idx}" for idx in range(len(entity_ids)))
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
        """Fuse FTS and vector rankings using reciprocal rank fusion."""
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
        for permalink, fused_score in ranked[offset : offset + limit]:
            output.append(replace(rows_by_permalink[permalink], score=fused_score))
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
        """Search across all indexed content using SQLite FTS5."""
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
                conditions.append(
                    "(search_index.title MATCH :text OR search_index.content_stems MATCH :text)"
                )

        # Handle title match search
        if title:
            title_text = self._prepare_search_term(title.strip(), is_prefix=False)
            params["title_text"] = title_text
            conditions.append("search_index.title MATCH :title_text")

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
                    conditions.append("search_index.permalink MATCH :permalink")

        # Handle entity type filter
        if search_item_types:
            type_list = ", ".join(f"'{t.value}'" for t in search_item_types)
            conditions.append(f"search_index.type IN ({type_list})")

        # Handle type filter
        if types:
            type_list = ", ".join(f"'{t}'" for t in types)
            conditions.append(
                f"json_extract(search_index.metadata, '$.entity_type') IN ({type_list})"
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
