"""Built-in sqlite-vec implementation of the semantic vector index contract."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

from loguru import logger
from sqlalchemy import text
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.models.search import create_sqlite_search_vector_embeddings
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError
from basic_memory.repository.semantic_vector_index import (
    SemanticVectorIndex,
    VectorIndexScope,
    VectorKey,
    VectorMatch,
    VectorRecord,
    validate_query_dimensions,
    validate_vector_dimensions,
)


SQLITE_VEC_MAX_K = 4096


class SQLiteVecIndex(SemanticVectorIndex):
    """Persist and query semantic vectors in SQLite with sqlite-vec."""

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        scope: VectorIndexScope,
    ) -> None:
        self._session_maker = session_maker
        self.scope = scope
        self._initialized = False
        self._initialize_lock = asyncio.Lock()
        self._load_lock = asyncio.Lock()

    def invalidate_initialization(self) -> None:
        """Require the next operation to revalidate lazily created vec storage."""
        self._initialized = False

    async def _ensure_loaded(self, session: AsyncSession) -> None:
        try:
            await session.execute(text("SELECT vec_version()"))
            return
        except SAOperationalError:
            pass

        try:
            import sqlite_vec
        except ImportError as exc:
            raise SemanticDependenciesMissingError(
                "sqlite-vec package is missing. Install/update basic-memory to include "
                "semantic dependencies: pip install -U basic-memory"
            ) from exc

        async with self._load_lock:
            try:
                await session.execute(text("SELECT vec_version()"))
                return
            except SAOperationalError:
                pass

            connection = await session.connection()
            raw_connection = await connection.get_raw_connection()
            driver_connection = raw_connection.driver_connection
            if not hasattr(driver_connection, "enable_load_extension"):
                raise SemanticDependenciesMissingError(
                    "This Python build does not support SQLite extension loading "
                    "(no enable_load_extension on sqlite3.Connection). Reinstall "
                    "basic-memory under uv-managed or Homebrew Python, or disable "
                    "semantic search."
                )
            await driver_connection.enable_load_extension(True)
            await driver_connection.load_extension(sqlite_vec.loadable_path())
            await driver_connection.enable_load_extension(False)
            await session.execute(text("SELECT vec_version()"))

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return

            async with db.scoped_session(self._session_maker) as session:
                await self._ensure_loaded(session)
                result = await session.execute(
                    text(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'search_vector_embeddings'"
                    )
                )
                vector_sql = result.scalar()
                expected_dimensions = f"float[{self.scope.dimensions}]"
                dimensions_changed = bool(vector_sql and expected_dimensions not in vector_sql)
                if dimensions_changed:
                    logger.warning(
                        "Embedding dimension mismatch (expected {dimensions}); "
                        "recreating sqlite-vec storage",
                        dimensions=self.scope.dimensions,
                    )
                    await session.execute(text("DROP TABLE IF EXISTS search_vector_embeddings"))

                await session.execute(create_sqlite_search_vector_embeddings(self.scope.dimensions))
                if dimensions_changed:
                    await session.execute(
                        text(
                            "UPDATE search_vector_chunks SET embedding_status = 'pending' "
                            "WHERE vector_index = 'sqlite-vec'"
                        )
                    )
                await session.commit()
            self._initialized = True

    async def _rowids_by_key(
        self,
        session: AsyncSession,
        keys: Sequence[VectorKey],
    ) -> dict[VectorKey, int]:
        if not keys:
            return {}
        params: dict[str, object] = {"project_id": self.scope.project_id}
        predicates: list[str] = []
        for index, key in enumerate(keys):
            params[f"entity_id_{index}"] = key.entity_id
            params[f"chunk_key_{index}"] = key.chunk_key
            predicates.append(
                f"(entity_id = :entity_id_{index} AND chunk_key = :chunk_key_{index})"
            )
        result = await session.execute(
            text(
                "SELECT id, entity_id, chunk_key FROM search_vector_chunks "
                "WHERE project_id = :project_id AND (" + " OR ".join(predicates) + ")"
            ),
            params,
        )
        return {
            VectorKey(entity_id=int(row["entity_id"]), chunk_key=str(row["chunk_key"])): int(
                row["id"]
            )
            for row in result.mappings().all()
        }

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        if not records:
            return
        validate_vector_dimensions(self.scope, records)
        await self.initialize()
        async with db.scoped_session(self._session_maker) as session:
            await self._ensure_loaded(session)
            rowids_by_key = await self._rowids_by_key(session, [record.key for record in records])
            missing = [record.key for record in records if record.key not in rowids_by_key]
            if missing:
                raise RuntimeError(f"Vector manifest rows are missing for keys: {missing!r}")

            rowids = [rowids_by_key[record.key] for record in records]
            params = {f"rowid_{index}": rowid for index, rowid in enumerate(rowids)}
            placeholders = ", ".join(f":rowid_{index}" for index in range(len(rowids)))
            await session.execute(
                text(f"DELETE FROM search_vector_embeddings WHERE rowid IN ({placeholders})"),
                params,
            )
            await session.execute(
                text(
                    "INSERT INTO search_vector_embeddings (rowid, embedding) "
                    "VALUES (:rowid, :embedding)"
                ),
                [
                    {
                        "rowid": rowids_by_key[record.key],
                        "embedding": json.dumps(record.values),
                    }
                    for record in records
                ],
            )
            await session.commit()

    async def delete(self, keys: Sequence[VectorKey]) -> None:
        if not keys:
            return
        await self.initialize()
        async with db.scoped_session(self._session_maker) as session:
            await self._ensure_loaded(session)
            rowids = list((await self._rowids_by_key(session, keys)).values())
            if rowids:
                params = {f"rowid_{index}": rowid for index, rowid in enumerate(rowids)}
                placeholders = ", ".join(f":rowid_{index}" for index in range(len(rowids)))
                await session.execute(
                    text(f"DELETE FROM search_vector_embeddings WHERE rowid IN ({placeholders})"),
                    params,
                )
                await session.commit()

    async def delete_entity(self, entity_id: int) -> None:
        await self.initialize()
        async with db.scoped_session(self._session_maker) as session:
            await self._ensure_loaded(session)
            await session.execute(
                text(
                    "DELETE FROM search_vector_embeddings WHERE rowid IN ("
                    "SELECT id FROM search_vector_chunks "
                    "WHERE project_id = :project_id AND entity_id = :entity_id)"
                ),
                {"project_id": self.scope.project_id, "entity_id": entity_id},
            )
            await session.commit()

    async def delete_orphans(self, _live_keys: Sequence[VectorKey]) -> None:
        """Remove sqlite-vec rows absent from the current ready manifest scope."""
        await self.initialize()
        async with db.scoped_session(self._session_maker) as session:
            await self._ensure_loaded(session)
            # A vec row without any manifest has no remaining project owner.
            # Remove these globally before the project-scoped stale-state pass;
            # otherwise they can occupy sqlite-vec's top-k window forever.
            orphan_result = await session.execute(
                text(
                    "SELECT rowid FROM search_vector_embeddings "
                    "EXCEPT SELECT id FROM search_vector_chunks"
                )
            )
            orphan_rowids = [int(rowid) for rowid in orphan_result.scalars().all()]
            if orphan_rowids:
                params = {
                    f"orphan_rowid_{index}": rowid for index, rowid in enumerate(orphan_rowids)
                }
                placeholders = ", ".join(
                    f":orphan_rowid_{index}" for index in range(len(orphan_rowids))
                )
                await session.execute(
                    text(f"DELETE FROM search_vector_embeddings WHERE rowid IN ({placeholders})"),
                    params,
                )
            await session.execute(
                text(
                    "DELETE FROM search_vector_embeddings WHERE rowid IN ("
                    "SELECT id FROM search_vector_chunks "
                    "WHERE project_id = :project_id AND NOT ("
                    "vector_index = 'sqlite-vec' "
                    "AND embedding_model = :embedding_identity "
                    "AND embedding_status = 'ready'))"
                ),
                {
                    "project_id": self.scope.project_id,
                    "embedding_identity": self.scope.embedding_identity,
                },
            )
            await session.commit()

    async def search(
        self,
        query: Sequence[float],
        *,
        limit: int,
    ) -> list[VectorMatch]:
        if not query or limit <= 0:
            return []
        validate_query_dimensions(self.scope, query)
        await self.initialize()
        vector_k = min(limit, SQLITE_VEC_MAX_K)
        async with db.scoped_session(self._session_maker) as session:
            await self._ensure_loaded(session)
            result = await session.execute(
                text(
                    "WITH vector_matches AS ("
                    " SELECT rowid, distance FROM search_vector_embeddings "
                    " WHERE embedding MATCH :query AND k = :vector_k"
                    ") "
                    "SELECT c.entity_id, c.chunk_key, vector_matches.distance "
                    "FROM vector_matches "
                    "JOIN search_vector_chunks c ON c.id = vector_matches.rowid "
                    "WHERE c.project_id = :project_id "
                    "AND c.vector_index = 'sqlite-vec' "
                    "AND c.embedding_status = 'ready' "
                    "AND c.embedding_model = :embedding_identity "
                    "ORDER BY vector_matches.distance ASC, "
                    "c.entity_id ASC, c.chunk_key ASC LIMIT :limit"
                ),
                {
                    "query": json.dumps(list(query)),
                    "vector_k": vector_k,
                    "project_id": self.scope.project_id,
                    "embedding_identity": self.scope.embedding_identity,
                    "limit": limit,
                },
            )
        return [
            VectorMatch(
                key=VectorKey(
                    entity_id=int(row["entity_id"]),
                    chunk_key=str(row["chunk_key"]),
                ),
                similarity=max(
                    0.0,
                    min(1.0, 1.0 - (float(row["distance"]) ** 2) / 2.0),
                ),
            )
            for row in result.mappings().all()
        ]
