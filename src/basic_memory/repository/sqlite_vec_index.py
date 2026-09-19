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
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError
from basic_memory.repository.semantic_vector_index import (
    VectorDeletion,
    VectorIndexScope,
    VectorKey,
    VectorMatch,
    VectorRecord,
    validate_query_dimensions,
    validate_vector_dimensions,
)


SQLITE_VEC_MAX_K = 4096
SQLITE_DELETE_BATCH_SIZE = 500


class SQLiteVecIndex:
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
            try:
                await driver_connection.enable_load_extension(True)
            except AttributeError as exc:
                # aiosqlite exposes the wrapper method even when the wrapped
                # sqlite3.Connection was built without extension support, so
                # calling it is the authoritative probe (#711).
                raise SemanticDependenciesMissingError(
                    "This Python build does not support SQLite extension loading "
                    "(no enable_load_extension on sqlite3.Connection). Reinstall "
                    "basic-memory under uv-managed or Homebrew Python, or disable "
                    "semantic search."
                ) from exc
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
                storage_missing = not vector_sql
                expected_dimensions = f"float[{self.scope.dimensions}]"
                dimensions_changed = bool(vector_sql and expected_dimensions not in vector_sql)
                source_hash_missing = bool(vector_sql and "+source_hash text" not in vector_sql)
                partitions_missing = bool(vector_sql and "partition key" not in vector_sql)
                if dimensions_changed or source_hash_missing:
                    logger.warning(
                        "SQLite vector storage schema mismatch "
                        "(expected dimensions={dimensions}, "
                        "source_hash_missing={source_hash_missing}); recreating storage",
                        dimensions=self.scope.dimensions,
                        source_hash_missing=source_hash_missing,
                    )
                    await session.execute(text("DROP TABLE IF EXISTS search_vector_embeddings"))
                elif partitions_missing:
                    # Trigger: storage predates the project_id partition key, and its
                    #   vectors are otherwise current.
                    # Why: vec0 cannot add a column in place, and re-embedding a whole
                    #   vault only to change how rows are partitioned would cost every
                    #   local user a full embedding pass for nothing new.
                    # Outcome: the vectors are carried into partitioned storage, each
                    #   keyed by the project its manifest row names; manifests stay ready.
                    logger.info(
                        "SQLite vector storage predates project partitions; "
                        "carrying vectors into partitioned storage"
                    )
                    await self._partition_existing_storage(session)

                await session.execute(create_sqlite_search_vector_embeddings(self.scope.dimensions))
                # Missing or dimension-rebuilt vec storage has no vectors, so ready
                # manifests must become pending before incremental sync inspects them.
                if storage_missing or dimensions_changed or source_hash_missing:
                    await session.execute(
                        text(
                            "UPDATE search_vector_chunks SET embedding_status = 'pending' "
                            "WHERE vector_index = 'sqlite-vec'"
                        )
                    )
                await session.commit()
            self._initialized = True

    async def _partition_existing_storage(self, session: AsyncSession) -> None:
        """Rebuild vec storage with the partition key, keeping every current vector.

        SQLite DDL is transactional, so the copy out, drop, recreate, and copy back
        either all land or none do. A vector whose manifest row is gone has no
        project to file under and is left behind, which is what the orphan sweep
        would have done to it anyway.
        """
        await session.execute(
            text(
                "CREATE TEMP TABLE search_vector_embeddings_carry AS "
                "SELECT e.rowid AS id, c.project_id AS project_id, "
                "e.embedding AS embedding, e.source_hash AS source_hash "
                "FROM search_vector_embeddings e "
                "JOIN search_vector_chunks c ON c.id = e.rowid"
            )
        )
        await session.execute(text("DROP TABLE search_vector_embeddings"))
        await session.execute(create_sqlite_search_vector_embeddings(self.scope.dimensions))
        await session.execute(
            text(
                "INSERT INTO search_vector_embeddings "
                "(rowid, project_id, embedding, source_hash) "
                "SELECT id, project_id, embedding, source_hash "
                "FROM search_vector_embeddings_carry"
            )
        )
        await session.execute(text("DROP TABLE search_vector_embeddings_carry"))

    async def upsert(self, project_id: int, records: Sequence[VectorRecord]) -> None:
        if not records:
            return
        validate_vector_dimensions(self.scope, records)
        await self.initialize()
        async with db.scoped_session(self._session_maker) as session:
            await self._ensure_loaded(session)
            params: dict[str, object] = {"project_id": project_id}
            predicates: list[str] = []
            records_by_key = {record.key: record for record in records}
            for index, record in enumerate(records):
                params[f"entity_id_{index}"] = record.key.entity_id
                params[f"chunk_key_{index}"] = record.key.chunk_key
                params[f"source_hash_{index}"] = record.source_hash
                predicates.append(
                    f"(entity_id = :entity_id_{index} "
                    f"AND chunk_key = :chunk_key_{index} "
                    f"AND source_hash = :source_hash_{index})"
                )

            # SQLite has no SELECT FOR UPDATE. This conditional no-op write
            # acquires the database write lock and verifies the source generation
            # before vec0 rows can be replaced in the same transaction.
            result = await session.execute(
                text(
                    "UPDATE search_vector_chunks SET source_hash = source_hash "
                    "WHERE project_id = :project_id AND ("
                    + " OR ".join(predicates)
                    + ") RETURNING id, entity_id, chunk_key"
                ),
                params,
            )
            rowids_by_key = {
                VectorKey(
                    entity_id=int(row["entity_id"]),
                    chunk_key=str(row["chunk_key"]),
                ): int(row["id"])
                for row in result.mappings().all()
            }
            current_records = [
                records_by_key[key] for key in rowids_by_key if key in records_by_key
            ]
            if not current_records:
                return

            rowids = [rowids_by_key[record.key] for record in current_records]
            params = {f"rowid_{index}": rowid for index, rowid in enumerate(rowids)}
            placeholders = ", ".join(f":rowid_{index}" for index in range(len(rowids)))
            await session.execute(
                text(f"DELETE FROM search_vector_embeddings WHERE rowid IN ({placeholders})"),
                params,
            )
            await session.execute(
                text(
                    "INSERT INTO search_vector_embeddings "
                    "(rowid, project_id, embedding, source_hash) "
                    "VALUES (:rowid, :project_id, :embedding, :source_hash)"
                ),
                [
                    {
                        "rowid": rowids_by_key[record.key],
                        "project_id": project_id,
                        "embedding": json.dumps(record.values),
                        "source_hash": record.source_hash,
                    }
                    for record in current_records
                ],
            )
            await session.commit()

    async def delete(self, project_id: int, records: Sequence[VectorDeletion]) -> None:
        if not records:
            return
        await self.initialize()
        async with db.scoped_session(self._session_maker) as session:
            await self._ensure_loaded(session)
            params: dict[str, object] = {"project_id": project_id}
            predicates: list[str] = []
            for index, record in enumerate(records):
                params[f"entity_id_{index}"] = record.key.entity_id
                params[f"chunk_key_{index}"] = record.key.chunk_key
                params[f"source_hash_{index}"] = record.source_hash
                predicates.append(
                    f"(entity_id = :entity_id_{index} "
                    f"AND chunk_key = :chunk_key_{index} "
                    f"AND source_hash = :source_hash_{index} "
                    "AND embedding_status = 'pending')"
                )
            result = await session.execute(
                text(
                    "UPDATE search_vector_chunks SET source_hash = source_hash "
                    "WHERE project_id = :project_id AND ("
                    + " OR ".join(predicates)
                    + ") RETURNING id"
                ),
                params,
            )
            rowids = [int(row_id) for row_id in result.scalars().all()]
            if rowids:
                params = {f"rowid_{index}": rowid for index, rowid in enumerate(rowids)}
                placeholders = ", ".join(f":rowid_{index}" for index in range(len(rowids)))
                await session.execute(
                    text(f"DELETE FROM search_vector_embeddings WHERE rowid IN ({placeholders})"),
                    params,
                )
                await session.execute(
                    text(f"DELETE FROM search_vector_chunks WHERE id IN ({placeholders})"),
                    params,
                )
                await session.commit()

    async def delete_entity(self, project_id: int, entity_id: int) -> None:
        await self.initialize()
        async with db.scoped_session(self._session_maker) as session:
            await self._ensure_loaded(session)
            await session.execute(
                text(
                    "DELETE FROM search_vector_embeddings WHERE rowid IN ("
                    "SELECT id FROM search_vector_chunks "
                    "WHERE project_id = :project_id AND entity_id = :entity_id)"
                ),
                {"project_id": project_id, "entity_id": entity_id},
            )
            await session.commit()

    async def delete_orphans(self, project_id: int, _live_keys: Sequence[VectorKey]) -> None:
        """Remove sqlite-vec rows absent from one project's current ready manifest."""
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
            stale_result = await session.execute(
                text(
                    "SELECT chunks.id FROM search_vector_chunks AS chunks "
                    "JOIN search_vector_embeddings AS embeddings "
                    "ON embeddings.rowid = chunks.id "
                    "WHERE chunks.project_id = :project_id AND NOT ("
                    "chunks.vector_index = 'sqlite-vec' "
                    "AND chunks.embedding_model = :embedding_identity "
                    "AND embeddings.source_hash = chunks.source_hash "
                    "AND chunks.embedding_status = 'ready')"
                ),
                {
                    "project_id": project_id,
                    "embedding_identity": self.scope.embedding_identity,
                },
            )
            stale_rowids = [int(rowid) for rowid in stale_result.scalars().all()]

            # sqlite-vec evaluates a correlated DELETE by repeatedly scanning the
            # virtual table. Resolve rowids from the indexed manifest first, then
            # delete by primary rowid in bounded batches.
            rowids = orphan_rowids + stale_rowids
            for offset in range(0, len(rowids), SQLITE_DELETE_BATCH_SIZE):
                batch = rowids[offset : offset + SQLITE_DELETE_BATCH_SIZE]
                params = {f"rowid_{index}": rowid for index, rowid in enumerate(batch)}
                placeholders = ", ".join(f":rowid_{index}" for index in range(len(batch)))
                await session.execute(
                    text(f"DELETE FROM search_vector_embeddings WHERE rowid IN ({placeholders})"),
                    params,
                )
            await session.commit()

    async def search(
        self,
        query: Sequence[float],
        *,
        limit: int,
        projects: ProjectScope,
    ) -> list[VectorMatch]:
        if not query or limit <= 0 or projects.is_empty:
            return []
        validate_query_dimensions(self.scope, query)
        await self.initialize()
        vector_k = min(limit, SQLITE_VEC_MAX_K)
        params: dict[str, object] = {
            "query": json.dumps(list(query)),
            "vector_k": vector_k,
            "embedding_identity": self.scope.embedding_identity,
            "limit": limit,
        }
        # vec0 ranks the k nearest within each partition in scope, so a small
        # project is never crowded out of its own window by a larger neighbour that
        # shares the database; the outer ORDER BY merges the partitions.
        partitions_in_scope = projects.predicate("project_id", params)
        async with db.scoped_session(self._session_maker) as session:
            await self._ensure_loaded(session)
            result = await session.execute(
                text(
                    "WITH vector_matches AS MATERIALIZED ("
                    " SELECT rowid, distance, source_hash FROM search_vector_embeddings "
                    f" WHERE {partitions_in_scope} "
                    " AND embedding MATCH :query AND k = :vector_k"
                    ") "
                    "SELECT c.entity_id, c.chunk_key, vector_matches.distance "
                    "FROM vector_matches "
                    "JOIN search_vector_chunks c ON c.id = vector_matches.rowid "
                    "AND c.source_hash = vector_matches.source_hash "
                    "WHERE c.vector_index = 'sqlite-vec' "
                    "AND c.embedding_status = 'ready' "
                    "AND c.embedding_model = :embedding_identity "
                    "ORDER BY vector_matches.distance ASC, "
                    "c.entity_id ASC, c.chunk_key ASC LIMIT :limit"
                ),
                params,
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
