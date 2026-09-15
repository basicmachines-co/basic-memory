"""SQLite FTS5-based search repository implementation."""

import asyncio
from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import override, List

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
)
from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.embedding_provider_factory import create_embedding_provider
from basic_memory.repository.rerank_provider import RerankProvider
from basic_memory.repository.rerank_provider_factory import create_rerank_provider
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_repository_base import (
    SearchRepositoryBase,
)
from basic_memory.repository.sqlite_search_query import SQLiteFts
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError
from basic_memory.repository.semantic_vector_index import SemanticVectorIndex
from basic_memory.repository.semantic_vector_sync import StagedVectorDeletion
from basic_memory.repository.semantic_vector_index_factory import build_vector_index_scope
from basic_memory.repository.sqlite_vec_index import SQLiteVecIndex


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
        vector_index_name: str | None = None,
        vector_index: SemanticVectorIndex | None = None,
        rerank_provider: RerankProvider | None = None,
    ):
        super().__init__(session_maker, project_id)
        self._fts = SQLiteFts(session_maker)
        self._app_config = app_config or ConfigManager().config
        self._semantic_enabled = self._app_config.semantic_search_enabled
        self._semantic_vector_k = self._app_config.semantic_vector_k
        self._semantic_min_similarity = self._app_config.semantic_min_similarity
        self._semantic_embedding_sync_batch_size = (
            self._app_config.semantic_embedding_sync_batch_size
        )
        self._embedding_provider = embedding_provider
        self._semantic_vector_index_name = vector_index_name or "sqlite-vec"
        self._rerank_provider = rerank_provider
        self._reranker_candidates = self._app_config.reranker_candidates
        self._reranker_max_document_chars = self._app_config.reranker_max_document_chars
        self._sqlite_vec_load_lock = asyncio.Lock()
        self._sqlite_prepare_write_lock = asyncio.Lock()
        self._vector_tables_initialized = False
        self._vector_dimensions = 384

        if self._semantic_enabled and self._embedding_provider is None:
            # Constraint: SQLite maps L2 distance to cosine similarity via 1 - L2²/2.
            # This conversion is correct only for unit-normalized embeddings.
            # Provider implementations must return normalized vectors.
            self._embedding_provider = create_embedding_provider(self._app_config)
        # create_rerank_provider returns None unless reranking is enabled.
        if self._semantic_enabled and self._rerank_provider is None:
            self._rerank_provider = create_rerank_provider(self._app_config)
        if self._embedding_provider is not None:
            self._vector_dimensions = self._embedding_provider.dimensions
            self._semantic_vector_index = vector_index or SQLiteVecIndex(
                session_maker,
                build_vector_index_scope(
                    self._app_config,
                    self._embedding_provider,
                    project_id,
                ),
            )

    @override
    async def init_search_index(self):
        """Create FTS5 virtual table for search if it doesn't exist.

        Uses CREATE VIRTUAL TABLE IF NOT EXISTS to preserve existing indexed data
        across server restarts. Also creates vector tables when semantic search
        is enabled so missing dependencies are caught at startup, not first query.
        """
        logger.debug("Initializing SQLite FTS5 search index")
        try:
            async with db.scoped_session(self.session_maker) as session:
                # Create FTS5 virtual table if it doesn't exist
                await session.execute(CREATE_SEARCH_INDEX)
                await session.commit()
        except Exception as e:  # pragma: no cover
            logger.error(f"Error initializing search index: {e}")
            raise e

        # Fail fast: create vector tables at startup so missing sqlite-vec
        # or embedding provider errors surface immediately.
        # Trigger: the runtime semantic stack (sqlite-vec extension or embedding
        #          provider) is unavailable at startup.
        # Why: failing the whole MCP boot for a search-only feature blocks
        #      Claude Desktop's handshake (#711). Keyword-only search is a
        #      reasonable fallback while the user resolves the dependency.
        # Outcome: log the cause, mark this repository as semantic-disabled so
        #      downstream calls short-circuit cleanly, and let init complete.
        if self._semantic_enabled:
            try:
                await self._ensure_vector_tables()
            except SemanticDependenciesMissingError as exc:
                logger.warning(
                    f"Semantic search disabled: {exc}. Falling back to keyword-only search."
                )
                self._semantic_enabled = False

    @override
    async def semantic_effectively_enabled(self) -> bool:
        """Probe the sqlite-vec runtime instead of trusting still-enabled config.

        init_search_index() disables semantics only on the startup repository
        instance; per-request instances are rebuilt from config, so they must
        re-check the runtime to honor the keyword-only fallback (#711).
        """
        if not self._semantic_enabled:
            return False
        async with db.scoped_session(self.session_maker) as session:
            try:
                await self._ensure_sqlite_vec_loaded(session)
            except SemanticDependenciesMissingError:
                return False
        return True

    @override
    async def get_entity_physical_chunk_keys(self, entity_id: int) -> set[str] | None:
        """Return chunk keys whose sqlite-vec physical row is live for this entity."""
        # Trigger: semantic search is off, or the configured index is external.
        # Why: a disabled config expects no physical rows, and external adapters expose
        # no portable storage-inspection contract (same rule as get_embedding_status()).
        # Outcome: None — chunk status stays manifest-only.
        if not self._semantic_enabled or self._semantic_vector_index_name != "sqlite-vec":
            return None
        async with db.scoped_session(self.session_maker) as session:
            tables_result = await session.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name IN ('search_vector_chunks', 'search_vector_embeddings')"
                )
            )
            table_names = {str(name) for name in tables_result.scalars().all()}
            if not {"search_vector_chunks", "search_vector_embeddings"} <= table_names:
                return set()
            try:
                await self._ensure_sqlite_vec_loaded(session)
            except SemanticDependenciesMissingError:
                # Runtime fallback: tables remain from a working install but this host
                # cannot load sqlite-vec, so physical storage is not inspectable here.
                return None
            result = await session.execute(
                text(
                    "SELECT c.chunk_key FROM search_vector_chunks c "
                    "JOIN search_vector_embeddings e "
                    "ON e.rowid = c.id AND e.source_hash = c.source_hash "
                    "WHERE c.project_id = :project_id AND c.entity_id = :entity_id"
                ),
                {"project_id": self.project_id, "entity_id": entity_id},
            )
            return {str(chunk_key) for chunk_key in result.scalars().all()}

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
            import sqlite_vec
        except ImportError as exc:
            raise SemanticDependenciesMissingError(
                "sqlite-vec package is missing. "
                "Install/update basic-memory to include semantic dependencies: "
                "pip install -U basic-memory"
            ) from exc

        # Trigger: sqlite-vec must be loaded on each SQLite connection before
        # vec tables and functions are visible.
        # Why: extension loading is connection-local, so we need one narrow
        # critical section to avoid racing two coroutines on the same step.
        # Outcome: connection setup stays serialized without blocking unrelated
        # prepare work behind the write-side lock.
        async with self._sqlite_vec_load_lock:
            try:
                await session.execute(text("SELECT vec_version()"))
                return
            except SAOperationalError:
                pass

            async_connection = await session.connection()
            raw_connection = await async_connection.get_raw_connection()
            driver_connection = raw_connection.driver_connection

            # Trigger: the underlying CPython was built without sqlite extension support.
            # Why: python.org's macOS installer ships a stripped sqlite3 module with no
            #      enable_load_extension; when uvx happens to pick that interpreter (#711),
            #      the AttributeError surfaces here and previously crashed startup before
            #      Claude Desktop could complete its MCP handshake.
            # Outcome: convert to SemanticDependenciesMissingError so the init-time
            #      handler can degrade gracefully to keyword search instead of dying.
            if not hasattr(driver_connection, "enable_load_extension"):
                raise SemanticDependenciesMissingError(
                    "This Python build does not support SQLite extension loading "
                    "(no enable_load_extension on sqlite3.Connection). "
                    "Common cause: python.org Python on macOS. "
                    "Reinstall basic-memory under a Python that ships extension "
                    "support (uv-managed CPython, Homebrew Python, or the official "
                    "Docker image), or set semantic_search_enabled=false in config "
                    "to silence this and use keyword-only search."
                )

            try:
                await driver_connection.enable_load_extension(True)
            except AttributeError as exc:
                # Trigger: aiosqlite exposes its wrapper method, but the wrapped
                # sqlite3.Connection was built without extension-loading support.
                # Why: hasattr() above can only inspect the wrapper, so invoking the
                # method is the authoritative capability probe on these interpreters.
                # Outcome: preserve the same keyword-only fallback as a directly
                # unsupported driver connection instead of leaking AttributeError.
                raise SemanticDependenciesMissingError(
                    "This Python build does not support SQLite extension loading "
                    "(no enable_load_extension on sqlite3.Connection). "
                    "Common cause: python.org Python on macOS. "
                    "Reinstall basic-memory under a Python that ships extension "
                    "support (uv-managed CPython, Homebrew Python, or the official "
                    "Docker image), or set semantic_search_enabled=false in config "
                    "to silence this and use keyword-only search."
                ) from exc
            await driver_connection.load_extension(sqlite_vec.loadable_path())
            await driver_connection.enable_load_extension(False)
            await session.execute(text("SELECT vec_version()"))

    # ------------------------------------------------------------------
    # Abstract hook implementations (vector/semantic, SQLite-specific)
    # ------------------------------------------------------------------

    @override
    async def _ensure_vector_tables(self) -> None:
        self._assert_semantic_available()
        if not hasattr(self, "_semantic_vector_index"):
            assert self._embedding_provider is not None
            self._semantic_vector_index = SQLiteVecIndex(
                self.session_maker,
                build_vector_index_scope(
                    self._app_config,
                    self._embedding_provider,
                    self.project_id,
                ),
            )
        if self._vector_tables_initialized:
            return

        logger.debug("Ensuring SQLite vector tables exist for semantic search")

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
                "entity_fingerprint",
                "embedding_model",
                "vector_index",
                "embedding_status",
                "updated_at",
            }
            schema_mismatch = bool(chunks_columns) and set(chunks_columns) != expected_columns
            if schema_mismatch:
                # Trigger: older SQLite installs are missing newly required chunk metadata columns.
                # Why: vector tables store derived data only, so rebuilding them is safer than
                # attempting piecemeal ALTER TABLE compatibility across sqlite-vec upgrades.
                # Outcome: first startup after the schema change forces a clean re-embed.
                logger.warning("search_vector_chunks schema mismatch, recreating vector tables")
                await session.execute(text("DROP TABLE IF EXISTS search_vector_embeddings"))
                await session.execute(text("DROP TABLE IF EXISTS search_vector_chunks"))
                if isinstance(self._semantic_vector_index, SQLiteVecIndex):
                    self._semantic_vector_index.invalidate_initialization()

            await session.execute(CREATE_SQLITE_SEARCH_VECTOR_CHUNKS)
            await session.execute(CREATE_SQLITE_SEARCH_VECTOR_CHUNKS_PROJECT_ENTITY)
            await session.execute(CREATE_SQLITE_SEARCH_VECTOR_CHUNKS_UNIQUE)

            # Trigger: legacy table from previous semantic implementation exists.
            # Why: old schema stores JSON vectors in a normal table and conflicts with sqlite-vec.
            # Outcome: remove disposable derived data so chunk/vector schema is deterministic.
            await session.execute(text("DROP TABLE IF EXISTS search_vector_index"))

            await session.commit()

        await self._semantic_vector_index.initialize()

        logger.debug(f"SQLite vector tables ready (dimensions={self._vector_dimensions})")
        self._vector_tables_initialized = True

    @override
    async def _prepare_vector_session(self, session: AsyncSession) -> None:
        """Load sqlite-vec extension for the session."""
        await self._ensure_sqlite_vec_loaded(session)

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
    async def _delete_project_builtin_vector_rows(self, session: AsyncSession) -> None:
        """Delete sqlite-vec rows atomically with their project manifest."""
        table_result = await session.execute(
            text(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('search_vector_chunks', 'search_vector_embeddings')"
            )
        )
        table_sql = {str(name): str(sql or "") for name, sql in table_result.all()}
        if not {"search_vector_chunks", "search_vector_embeddings"}.issubset(table_sql):
            return

        vector_sql = table_sql["search_vector_embeddings"]
        if "using vec0" in vector_sql.lower():
            await self._ensure_sqlite_vec_loaded(session)
        await session.execute(
            text(
                "DELETE FROM search_vector_embeddings WHERE rowid IN ("
                "SELECT id FROM search_vector_chunks WHERE project_id = :project_id)"
            ),
            {"project_id": self.project_id},
        )

    async def drop_vector_tables(self) -> None:
        """Drop SQLite vector tables on a sqlite-vec-enabled connection."""
        async with db.scoped_session(self.session_maker) as session:
            vector_sql_result = await session.execute(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'search_vector_embeddings'"
                )
            )
            vector_sql = vector_sql_result.scalar()
            if vector_sql and "using vec0" in vector_sql.lower():
                await self._ensure_sqlite_vec_loaded(session)

            await session.execute(text("DROP TABLE IF EXISTS search_vector_embeddings"))
            await session.execute(text("DROP TABLE IF EXISTS search_vector_chunks"))
            await session.execute(text("DROP TABLE IF EXISTS search_vector_index"))
            await session.commit()
        self._vector_tables_initialized = False

    @asynccontextmanager
    @override
    async def _prepare_entity_write_scope(self):
        """SQLite keeps the shared read window, but funnels prepare writes through one lock."""
        # Trigger: the shared prepare window fans out per entity after batched reads.
        # Why: SQLite still benefits from shared reads, but write transactions do
        # not get meaningfully faster when we open many at once.
        # Outcome: one entity at a time mutates chunk rows, while vec extension
        # loading uses its own separate lock and cannot deadlock this path.
        async with self._sqlite_prepare_write_lock:
            yield

    @override
    def _prepare_window_existing_rows_sql(self, placeholders: str) -> str:
        """Use the authoritative SQL manifest for adapter-independent readiness."""
        return super()._prepare_window_existing_rows_sql(placeholders)

    # ------------------------------------------------------------------
    # Index / bulk index overrides (FTS-only, no vector side-effects)
    # ------------------------------------------------------------------

    @override
    async def index_item(self, search_index_row: SearchIndexRow) -> None:
        """Index a single row in FTS only.

        Vector chunks are derived asynchronously via sync_entity_vectors().
        """
        await super().index_item(search_index_row)

    @override
    async def bulk_index_items(self, search_index_rows: List[SearchIndexRow]) -> None:
        """Index multiple rows in FTS only."""
        await super().bulk_index_items(search_index_rows)
