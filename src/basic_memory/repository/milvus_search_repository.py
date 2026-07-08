"""Milvus-backed semantic vector storage for search repositories."""

import asyncio
import importlib
import re
from typing import Any, Callable

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, ConfigManager, SemanticVectorBackend
from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_repository_base import (
    SearchRepositoryBase,
)
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository

type MilvusClientFactory = Callable[..., Any]

_COLLECTION_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MILVUS_ID_MAX_LENGTH = 128
_MILVUS_TEXT_MAX_LENGTH = 65535
_MILVUS_MARKER_COLUMNS = {"chunk_id", "project_id", "embedding_dims", "updated_at"}
_SQL_VECTOR_COLUMNS = {"embedding"}


def _load_milvus_symbols() -> tuple[MilvusClientFactory, Any]:
    """Load pymilvus lazily so the Milvus extra remains optional."""
    try:
        pymilvus = importlib.import_module("pymilvus")
    except ImportError as exc:
        raise SemanticDependenciesMissingError(
            "Milvus vector backend requires pymilvus. Install with "
            "`pip install 'basic-memory[milvus]'` or disable Milvus by setting "
            "semantic_vector_backend='database'."
        ) from exc
    return pymilvus.MilvusClient, pymilvus.DataType


async def delete_milvus_project_vectors(config: BasicMemoryConfig, project_id: int) -> None:
    """Delete one project's vectors from Milvus without a SearchRepository instance."""
    if config.semantic_vector_backend != SemanticVectorBackend.MILVUS:
        return

    client_factory, _data_type = _load_milvus_symbols()
    client_kwargs: dict[str, str] = {"uri": config.milvus_uri}
    if config.milvus_token:
        client_kwargs["token"] = config.milvus_token
    client = client_factory(**client_kwargs)
    collection_name = config.milvus_collection_name

    has_collection = await asyncio.to_thread(
        client.has_collection,
        collection_name=collection_name,
    )
    if not has_collection:
        return

    await asyncio.to_thread(
        client.delete,
        collection_name=collection_name,
        filter=f"project_id == {int(project_id)}",
    )


class MilvusVectorSearchMixin(SearchRepositoryBase):
    """Mixin that stores vector embeddings in Milvus and metadata in SQL."""

    _milvus_client_factory: MilvusClientFactory | None
    _milvus_data_type: Any | None
    _milvus_client: Any | None
    _milvus_uri: str
    _milvus_token: str | None
    _milvus_collection_name: str

    def _configure_milvus_vector_backend(
        self,
        app_config: BasicMemoryConfig,
        *,
        milvus_client_factory: MilvusClientFactory | None = None,
        milvus_data_type: Any | None = None,
    ) -> None:
        """Resolve Milvus runtime settings after the SQL repository initializes."""
        if not _COLLECTION_NAME_PATTERN.match(app_config.milvus_collection_name):
            raise ValueError(
                "Milvus collection names must start with a letter or underscore and "
                "contain only letters, numbers, and underscores."
            )

        self._milvus_uri = app_config.milvus_uri
        self._milvus_token = app_config.milvus_token
        self._milvus_collection_name = app_config.milvus_collection_name
        self._milvus_client_factory = milvus_client_factory
        self._milvus_data_type = milvus_data_type
        self._milvus_client = None

    def _get_milvus_symbols(self) -> tuple[MilvusClientFactory, Any]:
        if self._milvus_client_factory is not None and self._milvus_data_type is not None:
            return self._milvus_client_factory, self._milvus_data_type

        client_factory, data_type = _load_milvus_symbols()
        self._milvus_client_factory = client_factory
        self._milvus_data_type = data_type
        return client_factory, data_type

    def _get_milvus_client(self) -> Any:
        if self._milvus_client is not None:
            return self._milvus_client

        client_factory, _data_type = self._get_milvus_symbols()
        client_kwargs: dict[str, str] = {"uri": self._milvus_uri}
        if self._milvus_token:
            client_kwargs["token"] = self._milvus_token
        self._milvus_client = client_factory(**client_kwargs)
        return self._milvus_client

    async def _milvus_call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """Run synchronous MilvusClient calls off the event loop."""
        client = self._get_milvus_client()
        method = getattr(client, method_name)
        return await asyncio.to_thread(method, *args, **kwargs)

    async def _prepare_vector_session(self, session: AsyncSession) -> None:
        """Milvus does not need per-SQL-connection extension setup."""

    async def _ensure_vector_tables(self) -> None:
        """Create SQL marker tables and ensure the Milvus collection exists."""
        self._assert_semantic_available()
        if self._vector_tables_initialized:
            return

        logger.debug(
            "Ensuring Milvus vector backend is ready "
            f"(collection={self._milvus_collection_name}, dimensions={self._vector_dimensions})"
        )
        async with db.scoped_session(self.session_maker) as session:
            await self._ensure_sql_vector_metadata_tables(session)
            await session.commit()

        await self._ensure_milvus_collection()
        self._vector_tables_initialized = True

    async def _ensure_sql_vector_metadata_tables(self, session: AsyncSession) -> None:
        """Create derived SQL metadata used by the shared vector sync pipeline."""
        dialect_name = self._dialect_name(session)
        expected_chunk_columns = {
            "id",
            "entity_id",
            "project_id",
            "chunk_key",
            "chunk_text",
            "source_hash",
            "entity_fingerprint",
            "embedding_model",
            "updated_at",
        }
        chunk_columns = await self._table_columns(
            session, table_name="search_vector_chunks", dialect_name=dialect_name
        )
        chunk_schema_mismatch = bool(chunk_columns) and not expected_chunk_columns.issubset(
            chunk_columns
        )
        if chunk_schema_mismatch:
            logger.warning("search_vector_chunks schema mismatch, recreating vector metadata")
            await session.execute(text("DROP TABLE IF EXISTS search_vector_embeddings"))
            await session.execute(text("DROP TABLE IF EXISTS search_vector_chunks"))

        embedding_columns = await self._table_columns(
            session, table_name="search_vector_embeddings", dialect_name=dialect_name
        )
        marker_mismatch = bool(embedding_columns) and not self._is_milvus_marker_table(
            embedding_columns
        )
        if marker_mismatch:
            # Trigger: switching from sqlite-vec/pgvector to Milvus leaves a vector
            # storage table under the marker table name.
            # Why: the Milvus backend stores real vectors externally and only needs
            # SQL rows to track which chunks have been written.
            # Outcome: discard derived vector state; reindex recreates marker rows.
            logger.warning("search_vector_embeddings is not a Milvus marker table, recreating it")
            await session.execute(text("DROP TABLE IF EXISTS search_vector_embeddings"))

        if dialect_name == "postgresql":
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
            return

        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS search_vector_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_id INTEGER NOT NULL,
                    project_id INTEGER NOT NULL,
                    chunk_key TEXT NOT NULL,
                    chunk_text TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    entity_fingerprint TEXT NOT NULL,
                    embedding_model TEXT NOT NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
                    chunk_id INTEGER PRIMARY KEY,
                    project_id INTEGER NOT NULL,
                    embedding_dims INTEGER NOT NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
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

    @staticmethod
    def _is_milvus_marker_table(columns: set[str]) -> bool:
        """Return whether search_vector_embeddings is the Milvus SQL marker table."""
        return _MILVUS_MARKER_COLUMNS.issubset(columns) and not (_SQL_VECTOR_COLUMNS & columns)

    @staticmethod
    def _dialect_name(session: AsyncSession) -> str:
        if session.bind is None:
            return "sqlite"
        return str(session.bind.dialect.name)

    async def _table_columns(
        self,
        session: AsyncSession,
        *,
        table_name: str,
        dialect_name: str,
    ) -> set[str]:
        if dialect_name == "postgresql":
            result = await session.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = :table_name
                    """
                ),
                {"table_name": table_name},
            )
            return {str(row[0]) for row in result.fetchall()}

        result = await session.execute(text(f"PRAGMA table_info({table_name})"))
        return {str(row[1]) for row in result.fetchall()}

    async def _ensure_milvus_collection(self) -> None:
        has_collection = await self._milvus_call(
            "has_collection",
            collection_name=self._milvus_collection_name,
        )
        if has_collection:
            await self._validate_milvus_collection()
            return

        _client_factory, data_type = self._get_milvus_symbols()
        schema = await self._milvus_call(
            "create_schema",
            auto_id=False,
            enable_dynamic_field=False,
        )
        schema.add_field(
            field_name="id",
            datatype=data_type.VARCHAR,
            is_primary=True,
            max_length=_MILVUS_ID_MAX_LENGTH,
        )
        schema.add_field(field_name="project_id", datatype=data_type.INT64)
        schema.add_field(field_name="chunk_id", datatype=data_type.INT64)
        schema.add_field(field_name="entity_id", datatype=data_type.INT64)
        schema.add_field(
            field_name="chunk_key",
            datatype=data_type.VARCHAR,
            max_length=512,
        )
        schema.add_field(
            field_name="chunk_text",
            datatype=data_type.VARCHAR,
            max_length=_MILVUS_TEXT_MAX_LENGTH,
        )
        schema.add_field(
            field_name="embedding",
            datatype=data_type.FLOAT_VECTOR,
            dim=self._vector_dimensions,
        )

        index_params = await self._milvus_call("prepare_index_params")
        index_params.add_index(
            field_name="embedding",
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )
        await self._milvus_call(
            "create_collection",
            collection_name=self._milvus_collection_name,
            schema=schema,
            index_params=index_params,
        )

    async def _validate_milvus_collection(self) -> None:
        description = await self._milvus_call(
            "describe_collection",
            collection_name=self._milvus_collection_name,
        )
        fields = description.get("fields", []) if isinstance(description, dict) else []
        fields_by_name = {str(field.get("name")): field for field in fields if "name" in field}
        required_fields = {
            "id",
            "project_id",
            "chunk_id",
            "entity_id",
            "chunk_key",
            "chunk_text",
            "embedding",
        }
        missing_fields = sorted(required_fields - set(fields_by_name))
        if missing_fields:
            raise ValueError(
                f"Milvus collection '{self._milvus_collection_name}' is missing required "
                f"fields: {', '.join(missing_fields)}"
            )

        vector_field = fields_by_name["embedding"]
        params = vector_field.get("params", {})
        actual_dim = params.get("dim")
        if actual_dim is None:
            return
        if int(actual_dim) != self._vector_dimensions:
            raise ValueError(
                f"Milvus collection '{self._milvus_collection_name}' has embedding "
                f"dimension {actual_dim}, but Basic Memory is configured for "
                f"{self._vector_dimensions}. Use a new milvus_collection_name or recreate "
                "the collection before reindexing."
            )

    def _milvus_id(self, chunk_id: int) -> str:
        return f"{self.project_id}:{int(chunk_id)}"

    async def _run_vector_query(
        self,
        session: AsyncSession,
        query_embedding: list[float],
        candidate_limit: int,
    ) -> list[dict]:
        if not query_embedding:
            return []

        raw_results = await self._milvus_call(
            "search",
            collection_name=self._milvus_collection_name,
            data=[query_embedding],
            anns_field="embedding",
            limit=candidate_limit,
            filter=f"project_id == {int(self.project_id)}",
            search_params={"metric_type": "COSINE"},
            output_fields=["chunk_id", "entity_id", "chunk_key", "chunk_text"],
        )
        return self._milvus_search_rows(raw_results)

    @staticmethod
    def _milvus_search_rows(raw_results: Any) -> list[dict]:
        hits = raw_results[0] if raw_results and isinstance(raw_results[0], list) else raw_results
        rows: list[dict] = []
        for hit in hits or []:
            if not isinstance(hit, dict):
                continue
            entity = hit.get("entity")
            if not isinstance(entity, dict):
                entity = hit
            rows.append(
                {
                    "entity_id": int(entity["entity_id"]),
                    "chunk_key": str(entity["chunk_key"]),
                    "chunk_text": str(entity.get("chunk_text", "")),
                    "best_distance": float(hit.get("distance", hit.get("score", 0.0))),
                }
            )
        return rows

    async def _write_embeddings(
        self,
        session: AsyncSession,
        jobs: list[tuple[int, str]],
        embeddings: list[list[float]],
    ) -> None:
        if not jobs:
            return

        chunk_ids = [row_id for row_id, _ in jobs]
        chunk_rows = await self._fetch_chunk_rows(session, chunk_ids)
        records = []
        marker_rows = []
        for (chunk_id, chunk_text), embedding in zip(jobs, embeddings, strict=True):
            chunk_row = chunk_rows.get(chunk_id)
            if chunk_row is None:
                raise RuntimeError(f"Vector chunk row {chunk_id} disappeared before write.")
            records.append(
                {
                    "id": self._milvus_id(chunk_id),
                    "project_id": int(self.project_id),
                    "chunk_id": int(chunk_id),
                    "entity_id": int(chunk_row["entity_id"]),
                    "chunk_key": str(chunk_row["chunk_key"]),
                    "chunk_text": str(chunk_text)[:_MILVUS_TEXT_MAX_LENGTH],
                    "embedding": embedding,
                }
            )
            marker_rows.append(
                {
                    "chunk_id": int(chunk_id),
                    "project_id": int(self.project_id),
                    "embedding_dims": len(embedding),
                }
            )

        await self._milvus_call(
            "upsert",
            collection_name=self._milvus_collection_name,
            data=records,
        )
        await self._replace_marker_rows(session, marker_rows)

    async def _fetch_chunk_rows(
        self,
        session: AsyncSession,
        chunk_ids: list[int],
    ) -> dict[int, dict[str, Any]]:
        placeholders = ", ".join(f":chunk_id_{index}" for index in range(len(chunk_ids)))
        params: dict[str, object] = {
            "project_id": self.project_id,
            **{f"chunk_id_{index}": chunk_id for index, chunk_id in enumerate(chunk_ids)},
        }
        result = await session.execute(
            text(
                "SELECT id, entity_id, chunk_key, chunk_text "
                "FROM search_vector_chunks "
                f"WHERE project_id = :project_id AND id IN ({placeholders})"
            ),
            params,
        )
        return {int(row["id"]): dict(row) for row in result.mappings().all()}

    async def _replace_marker_rows(
        self,
        session: AsyncSession,
        marker_rows: list[dict[str, int]],
    ) -> None:
        chunk_ids = [row["chunk_id"] for row in marker_rows]
        await self._delete_marker_rows(session, chunk_ids)
        timestamp_expr = self._timestamp_now_expr()
        await session.execute(
            text(
                "INSERT INTO search_vector_embeddings "
                "(chunk_id, project_id, embedding_dims, updated_at) "
                f"VALUES (:chunk_id, :project_id, :embedding_dims, {timestamp_expr})"
            ),
            marker_rows,
        )

    async def _delete_marker_rows(self, session: AsyncSession, chunk_ids: list[int]) -> None:
        if not chunk_ids:
            return
        placeholders = ", ".join(f":chunk_id_{index}" for index in range(len(chunk_ids)))
        params = {f"chunk_id_{index}": chunk_id for index, chunk_id in enumerate(chunk_ids)}
        await session.execute(
            text(f"DELETE FROM search_vector_embeddings WHERE chunk_id IN ({placeholders})"),
            params,
        )

    async def _delete_milvus_ids(self, chunk_ids: list[int]) -> None:
        if not chunk_ids:
            return
        await self._milvus_call(
            "delete",
            collection_name=self._milvus_collection_name,
            ids=[self._milvus_id(chunk_id) for chunk_id in chunk_ids],
        )

    async def _delete_milvus_filter(self, filter_expr: str) -> None:
        await self._milvus_call(
            "delete",
            collection_name=self._milvus_collection_name,
            filter=filter_expr,
        )

    async def _delete_entity_chunks(
        self,
        session: AsyncSession,
        entity_id: int,
    ) -> None:
        await self._delete_milvus_filter(
            f"project_id == {int(self.project_id)} and entity_id == {int(entity_id)}"
        )
        await session.execute(
            text(
                "DELETE FROM search_vector_embeddings "
                "WHERE chunk_id IN ("
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
        await self._delete_milvus_ids(stale_ids)
        await self._delete_marker_rows(session, stale_ids)
        stale_placeholders = ", ".join(f":stale_id_{idx}" for idx in range(len(stale_ids)))
        stale_params = {
            "project_id": self.project_id,
            "entity_id": entity_id,
            **{f"stale_id_{idx}": row_id for idx, row_id in enumerate(stale_ids)},
        }
        await session.execute(
            text(
                "DELETE FROM search_vector_chunks "
                f"WHERE id IN ({stale_placeholders}) "
                "AND project_id = :project_id AND entity_id = :entity_id"
            ),
            stale_params,
        )

    async def delete_project_vector_rows(self) -> None:
        """Delete this project's SQL vector metadata and Milvus records."""
        await self._ensure_vector_tables()
        await self._delete_milvus_filter(f"project_id == {int(self.project_id)}")
        async with db.scoped_session(self.session_maker) as session:
            await session.execute(
                text("DELETE FROM search_vector_embeddings WHERE project_id = :project_id"),
                {"project_id": self.project_id},
            )
            await session.execute(
                text("DELETE FROM search_vector_chunks WHERE project_id = :project_id"),
                {"project_id": self.project_id},
            )
            await session.commit()

    async def delete_stale_vector_rows(self) -> None:
        """Delete stale SQL vector metadata and matching Milvus records."""
        await self._ensure_vector_tables()
        async with db.scoped_session(self.session_maker) as session:
            stale_result = await session.execute(
                text(
                    "SELECT id FROM search_vector_chunks "
                    "WHERE project_id = :project_id "
                    "AND entity_id NOT IN ("
                    "SELECT id FROM entity WHERE project_id = :project_id"
                    ")"
                ),
                {"project_id": self.project_id},
            )
            stale_ids = [int(row[0]) for row in stale_result.fetchall()]
            await self._delete_milvus_ids(stale_ids)
            await self._delete_marker_rows(session, stale_ids)
            await session.execute(
                text(
                    "DELETE FROM search_vector_chunks "
                    "WHERE project_id = :project_id "
                    "AND entity_id NOT IN ("
                    "SELECT id FROM entity WHERE project_id = :project_id"
                    ")"
                ),
                {"project_id": self.project_id},
            )
            await session.commit()

    async def drop_vector_tables(self) -> None:
        """Clear Milvus project records and drop derived SQL vector metadata."""
        await self._ensure_vector_tables()
        await self._delete_milvus_filter(f"project_id == {int(self.project_id)}")
        async with db.scoped_session(self.session_maker) as session:
            await session.execute(text("DROP TABLE IF EXISTS search_vector_embeddings"))
            await session.execute(text("DROP TABLE IF EXISTS search_vector_chunks"))
            await session.execute(text("DROP TABLE IF EXISTS search_vector_index"))
            await session.commit()
        self._vector_tables_initialized = False

    def _distance_to_similarity(self, distance: float) -> float:
        """Convert Milvus COSINE distance to Basic Memory's higher-is-better score."""
        return max(0.0, min(1.0, 1.0 - distance))


class SQLiteMilvusSearchRepository(MilvusVectorSearchMixin, SQLiteSearchRepository):
    """SQLite FTS repository with Milvus-backed semantic vectors."""

    def __init__(
        self,
        session_maker,
        project_id: int,
        app_config: BasicMemoryConfig | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        milvus_client_factory: MilvusClientFactory | None = None,
        milvus_data_type: Any | None = None,
    ):
        super().__init__(
            session_maker,
            project_id=project_id,
            app_config=app_config,
            embedding_provider=embedding_provider,
        )
        self._configure_milvus_vector_backend(
            app_config or ConfigManager().config,
            milvus_client_factory=milvus_client_factory,
            milvus_data_type=milvus_data_type,
        )


class PostgresMilvusSearchRepository(MilvusVectorSearchMixin, PostgresSearchRepository):
    """Postgres FTS repository with Milvus-backed semantic vectors."""

    def __init__(
        self,
        session_maker,
        project_id: int,
        app_config: BasicMemoryConfig | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        milvus_client_factory: MilvusClientFactory | None = None,
        milvus_data_type: Any | None = None,
    ):
        super().__init__(
            session_maker,
            project_id=project_id,
            app_config=app_config,
            embedding_provider=embedding_provider,
        )
        self._configure_milvus_vector_backend(
            app_config or ConfigManager().config,
            milvus_client_factory=milvus_client_factory,
            milvus_data_type=milvus_data_type,
        )


__all__ = [
    "PostgresMilvusSearchRepository",
    "SQLiteMilvusSearchRepository",
    "delete_milvus_project_vectors",
]
