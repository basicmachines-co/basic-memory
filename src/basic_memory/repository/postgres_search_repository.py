"""PostgreSQL tsvector-based search repository implementation."""

import asyncio
import json
from collections.abc import Sequence
from typing import Any, override, List

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, ConfigManager, DatabaseBackend
from basic_memory.models.search import SEARCH_INDEX_ROW_KEY_COLUMNS
from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.embedding_provider_factory import create_embedding_provider
from basic_memory.repository.rerank_provider import RerankProvider
from basic_memory.repository.rerank_provider_factory import create_rerank_provider
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.script_ngrams import build_script_ngrams
from basic_memory.repository.semantic_chunking import VectorChunkRecord
from basic_memory.repository.search_repository_base import (
    SearchRepositoryBase,
    VectorChunkState,
)
from basic_memory.repository.postgres_search_query import PostgresFts
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError
from basic_memory.repository.semantic_vector_index import SemanticVectorIndex
from basic_memory.repository.semantic_vector_sync import (
    PendingEmbeddingJob,
    StagedVectorDeletion,
)
from basic_memory.repository.semantic_vector_index_factory import (
    build_vector_index_scope,
    resolve_semantic_vector_index_name,
)
from basic_memory.repository.pgvector_index import PgVectorIndex
from basic_memory.repository.postgres_fts_chunks import split_postgres_fts_chunks


def _strip_nul_from_row(row_data: dict[str, Any]) -> dict[str, Any]:
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
    The partial unique index uix_search_index_permalink_type_project prevents a second
    row of the same kind from claiming an address that kind already owns.
    """

    def __init__(
        self,
        session_maker,
        project_id: int,
        app_config: BasicMemoryConfig | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        vector_index_name: str | None = None,
        vector_index: SemanticVectorIndex | None = None,
        rerank_provider: RerankProvider | None = None,
    ):
        super().__init__(session_maker, project_id)
        self._fts = PostgresFts(session_maker)
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
        self._rerank_provider = rerank_provider
        self._reranker_candidates = self._app_config.reranker_candidates
        self._reranker_max_document_chars = self._app_config.reranker_max_document_chars
        self._vector_dimensions = 384
        self._vector_tables_initialized = False
        self._vector_tables_lock = asyncio.Lock()

        if self._semantic_enabled and self._embedding_provider is None:
            self._embedding_provider = create_embedding_provider(self._app_config)
        # create_rerank_provider returns None unless reranking is enabled.
        if self._semantic_enabled and self._rerank_provider is None:
            self._rerank_provider = create_rerank_provider(self._app_config)
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
                    build_vector_index_scope(self._app_config, self._embedding_provider),
                )
            self._semantic_vector_index_name = effective_name
            self._semantic_vector_index = vector_index

    @override
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

    @override
    async def index_item(self, search_index_row: SearchIndexRow) -> None:
        """Index or update a single item using UPSERT.

        Uses INSERT ... ON CONFLICT to handle race conditions during parallel
        entity indexing. The partial unique index uix_search_index_permalink_type_project
        on (permalink, type, project_id) WHERE permalink IS NOT NULL prevents a second
        row of the same kind from claiming an address that kind already owns.

        For rows with non-null permalinks, a conflict against the same kind is resolved
        by updating the existing row. For rows with null permalinks, no conflict occurs
        on this partial index.
        """
        async with db.scoped_session(self.session_maker) as session:
            # Serialize JSON for raw SQL
            insert_data = search_index_row.to_insert(serialize_json=True)
            insert_data["project_id"] = self.project_id
            insert_data["script_ngrams"] = build_script_ngrams(
                search_index_row.title,
                search_index_row.content_stems,
            )
            insert_data = _strip_nul_from_row(insert_data)

            # Use upsert to handle race conditions during parallel indexing.
            # The conflict target matches uix_search_index_permalink_type_project, the
            # partial unique index over SEARCH_INDEX_ROW_KEY. For rows with NULL
            # permalinks no conflict occurs (the partial index doesn't apply).
            await session.execute(
                text(f"""
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
                    ON CONFLICT ({SEARCH_INDEX_ROW_KEY_COLUMNS}) WHERE permalink IS NOT NULL DO UPDATE SET
                        id = EXCLUDED.id,
                        title = EXCLUDED.title,
                        content_stems = EXCLUDED.content_stems,
                        content_snippet = EXCLUDED.content_snippet,
                        script_ngrams = EXCLUDED.script_ngrams,
                        file_path = EXCLUDED.file_path,
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
            await self._replace_fts_chunks(session, [search_index_row])
            logger.debug(f"indexed row {search_index_row}")
            await session.commit()

    async def _replace_fts_chunks(
        self,
        session: AsyncSession,
        search_index_rows: Sequence[SearchIndexRow],
    ) -> None:
        """Replace bounded full-content FTS rows for one indexing batch."""
        if not search_index_rows:
            return

        await session.execute(
            text("""
                DELETE FROM search_index_fts_chunks
                WHERE project_id = :project_id
                  AND (search_index_id, search_index_type) IN (
                      SELECT search_index_id, search_index_type
                      FROM unnest(
                          CAST(:search_index_ids AS INTEGER[]),
                          CAST(:search_index_types AS VARCHAR[])
                      ) AS indexed_rows(search_index_id, search_index_type)
                  )
            """),
            {
                "project_id": self.project_id,
                "search_index_ids": [row.id for row in search_index_rows],
                "search_index_types": [row.type for row in search_index_rows],
            },
        )

        chunks = [
            {
                "search_index_id": row.id,
                "search_index_type": row.type,
                "chunk_index": chunk_index,
                "chunk_text": chunk_text.replace("\x00", ""),
                "script_ngrams": build_script_ngrams(chunk_text.replace("\x00", "")),
            }
            for row in search_index_rows
            for chunk_index, chunk_text in split_postgres_fts_chunks(row.content_snippet)
        ]
        if not chunks:
            return

        await session.execute(
            text("""
                INSERT INTO search_index_fts_chunks (
                    project_id,
                    search_index_id,
                    search_index_type,
                    chunk_index,
                    chunk_text,
                    script_ngrams
                )
                SELECT
                    :project_id,
                    chunk.search_index_id,
                    chunk.search_index_type,
                    chunk.chunk_index,
                    chunk.chunk_text,
                    chunk.script_ngrams
                FROM jsonb_to_recordset(CAST(:chunks AS JSONB)) AS chunk(
                    search_index_id INTEGER,
                    search_index_type VARCHAR,
                    chunk_index INTEGER,
                    chunk_text TEXT,
                    script_ngrams TEXT
                )
            """),
            {"project_id": self.project_id, "chunks": json.dumps(chunks)},
        )

    # ------------------------------------------------------------------
    # Abstract hook implementations (vector/semantic, Postgres-specific)
    # ------------------------------------------------------------------

    @override
    async def get_entity_physical_chunk_keys(self, entity_id: int) -> set[str] | None:
        """Return chunk keys whose pgvector physical row is live for this entity."""
        # Trigger: semantic search is off, or the configured index is external.
        # Why: a disabled config expects no physical rows, and external adapters expose
        # no portable storage-inspection contract (same rule as get_embedding_status()).
        # Outcome: None — chunk status stays manifest-only.
        if not self._semantic_enabled or self._semantic_vector_index_name != "pgvector":
            return None
        async with db.scoped_session(self.session_maker) as session:
            tables_result = await session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = ANY (current_schemas(false)) "
                    "AND table_name IN ('search_vector_chunks', 'search_vector_embeddings')"
                )
            )
            table_names = {str(name) for name in tables_result.scalars().all()}
            if not {"search_vector_chunks", "search_vector_embeddings"} <= table_names:
                return set()
            result = await session.execute(
                text(
                    "SELECT c.chunk_key FROM search_vector_chunks c "
                    "JOIN search_vector_embeddings e "
                    "ON e.chunk_id = c.id AND e.source_hash = c.source_hash "
                    "WHERE c.project_id = :project_id AND c.entity_id = :entity_id"
                ),
                {"project_id": self.project_id, "entity_id": entity_id},
            )
            return {str(chunk_key) for chunk_key in result.scalars().all()}

    @override
    async def _ensure_vector_tables(self) -> None:
        self._assert_semantic_available()
        if not hasattr(self, "_semantic_vector_index"):
            assert self._embedding_provider is not None
            self._semantic_vector_index_name = "pgvector"
            self._semantic_vector_index = PgVectorIndex(
                self.session_maker,
                build_vector_index_scope(self._app_config, self._embedding_provider),
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

    @override
    def _vector_prepare_window_size(self) -> int:
        """Use a bounded config-driven prepare window for Postgres vector sync."""
        return self._semantic_postgres_prepare_concurrency

    @override
    async def _upsert_scheduled_chunk_records(
        self,
        session: AsyncSession,
        *,
        entity_id: int,
        scheduled_records: list[VectorChunkRecord],
        existing_by_key: dict[str, VectorChunkState],
        entity_fingerprint: str,
        embedding_model: str,
    ) -> list[PendingEmbeddingJob]:
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
            PendingEmbeddingJob(
                entity_id=entity_id,
                chunk_row_id=upserted_ids_by_key[record["chunk_key"]],
                chunk_key=record["chunk_key"],
                chunk_text=record["chunk_text"],
                source_hash=record["source_hash"],
            )
            for record in scheduled_records
        ]

    @override
    async def _delete_entity_chunks(
        self,
        session: AsyncSession,
        entity_id: int,
        *,
        expected_deletions: Sequence[StagedVectorDeletion] | None = None,
    ) -> list[StagedVectorDeletion]:
        return await super()._delete_entity_chunks(
            session,
            entity_id,
            expected_deletions=expected_deletions,
        )

    @override
    async def _delete_stale_chunks(
        self,
        session: AsyncSession,
        stale_ids: list[int],
        entity_id: int,
        *,
        expected_deletions: Sequence[StagedVectorDeletion] | None = None,
    ) -> list[StagedVectorDeletion]:
        return await super()._delete_stale_chunks(
            session,
            stale_ids,
            entity_id,
            expected_deletions=expected_deletions,
        )

    @override
    def _timestamp_now_expr(self) -> str:
        return "NOW()"

    # ------------------------------------------------------------------
    # Index / bulk index overrides (Postgres UPSERT)
    # ------------------------------------------------------------------

    @override
    async def bulk_index_items(self, search_index_rows: List[SearchIndexRow]) -> None:
        """Index multiple items in a single batch operation using UPSERT.

        Uses INSERT ... ON CONFLICT to handle race conditions during parallel
        entity indexing. The partial unique index uix_search_index_permalink_type_project
        on (permalink, type, project_id) WHERE permalink IS NOT NULL prevents a second
        row of the same kind from claiming an address that kind already owns.

        For rows with non-null permalinks, a conflict against the same kind is resolved
        by updating the existing row. For rows with null permalinks, the partial index
        doesn't apply and they are inserted directly.

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
                insert_data["script_ngrams"] = build_script_ngrams(
                    row.title,
                    row.content_stems,
                )
                insert_data_list.append(_strip_nul_from_row(insert_data))

            # Use upsert to handle race conditions during parallel indexing.
            # The conflict target matches uix_search_index_permalink_type_project, the
            # partial unique index over SEARCH_INDEX_ROW_KEY. For rows with NULL
            # permalinks no conflict occurs (the partial index doesn't apply).
            await session.execute(
                text(f"""
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
                    ON CONFLICT ({SEARCH_INDEX_ROW_KEY_COLUMNS}) WHERE permalink IS NOT NULL DO UPDATE SET
                        id = EXCLUDED.id,
                        title = EXCLUDED.title,
                        content_stems = EXCLUDED.content_stems,
                        content_snippet = EXCLUDED.content_snippet,
                        script_ngrams = EXCLUDED.script_ngrams,
                        file_path = EXCLUDED.file_path,
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
            await self._replace_fts_chunks(session, search_index_rows)
            logger.debug(f"Bulk indexed {len(search_index_rows)} rows")
            await session.commit()
