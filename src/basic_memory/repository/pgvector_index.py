"""Built-in pgvector implementation of the semantic vector index contract."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
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


class PgVectorIndex(SemanticVectorIndex):
    """Persist and query semantic vectors in PostgreSQL with pgvector."""

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        scope: VectorIndexScope,
    ) -> None:
        self._session_maker = session_maker
        self.scope = scope
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    @staticmethod
    def _format_vector(vector: Sequence[float]) -> str:
        values = ",".join(f"{float(value):.12g}" for value in vector)
        return f"[{values}]"

    async def initialize(self) -> None:
        if self._initialized:
            return

        async with self._initialize_lock:
            if self._initialized:
                return

            async with db.scoped_session(self._session_maker) as session:
                try:
                    await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                except Exception as exc:
                    raise SemanticDependenciesMissingError(
                        "pgvector extension is unavailable for this Postgres database."
                    ) from exc

                existing_dimensions = await self._existing_dimensions(session)
                dimensions_changed = (
                    existing_dimensions is not None and existing_dimensions != self.scope.dimensions
                )
                if dimensions_changed:
                    logger.warning(
                        "Embedding dimension mismatch: table has {existing}, "
                        "provider expects {expected}. Recreating vector storage.",
                        existing=existing_dimensions,
                        expected=self.scope.dimensions,
                    )
                    await session.execute(text("DROP TABLE IF EXISTS search_vector_embeddings"))

                await session.execute(
                    text(f"""
                        CREATE TABLE IF NOT EXISTS search_vector_embeddings (
                            chunk_id BIGINT PRIMARY KEY
                                REFERENCES search_vector_chunks(id) ON DELETE CASCADE,
                            project_id INTEGER NOT NULL,
                            embedding vector({self.scope.dimensions}) NOT NULL,
                            embedding_dims INTEGER NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                    """)
                )
                await session.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS "
                        "idx_search_vector_embeddings_project_dims "
                        "ON search_vector_embeddings (project_id, embedding_dims)"
                    )
                )
                await session.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS idx_search_vector_embeddings_hnsw "
                        "ON search_vector_embeddings "
                        "USING hnsw (embedding vector_cosine_ops) "
                        "WITH (m = 16, ef_construction = 64)"
                    )
                )

                # Trigger: pgvector's fixed-width column was recreated for a new model.
                # Why: SQL manifest rows can otherwise remain `ready` after their vectors
                # disappeared, causing the incremental sync to skip them forever.
                # Outcome: the normal sync pipeline re-embeds every affected chunk.
                if dimensions_changed:
                    await session.execute(
                        text(
                            "UPDATE search_vector_chunks SET embedding_status = 'pending' "
                            "WHERE vector_index = 'pgvector'"
                        )
                    )
                await session.commit()

            self._initialized = True

    async def _existing_dimensions(self, session: AsyncSession) -> int | None:
        exists = await session.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'search_vector_embeddings'"
            )
        )
        if exists.fetchone() is None:
            return None

        result = await session.execute(
            text(
                "SELECT atttypmod FROM pg_attribute "
                "WHERE attrelid = 'search_vector_embeddings'::regclass "
                "AND attname = 'embedding'"
            )
        )
        value = result.scalar_one_or_none()
        return int(value) if value is not None else None

    async def _chunk_ids_by_key(
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
            ids_by_key = await self._chunk_ids_by_key(session, [record.key for record in records])
            missing = [record.key for record in records if record.key not in ids_by_key]
            if missing:
                raise RuntimeError(f"Vector manifest rows are missing for keys: {missing!r}")

            params: dict[str, object] = {"project_id": self.scope.project_id}
            values: list[str] = []
            for index, record in enumerate(records):
                params[f"chunk_id_{index}"] = ids_by_key[record.key]
                params[f"embedding_{index}"] = self._format_vector(record.values)
                params[f"dimensions_{index}"] = len(record.values)
                values.append(
                    f"(:chunk_id_{index}, :project_id, "
                    f"CAST(:embedding_{index} AS vector), :dimensions_{index}, NOW())"
                )
            await session.execute(
                text(f"""
                    INSERT INTO search_vector_embeddings (
                        chunk_id, project_id, embedding, embedding_dims, updated_at
                    ) VALUES {", ".join(values)}
                    ON CONFLICT (chunk_id) DO UPDATE SET
                        project_id = EXCLUDED.project_id,
                        embedding = EXCLUDED.embedding,
                        embedding_dims = EXCLUDED.embedding_dims,
                        updated_at = NOW()
                """),
                params,
            )
            await session.commit()

    async def delete(self, keys: Sequence[VectorKey]) -> None:
        if not keys:
            return
        await self.initialize()
        async with db.scoped_session(self._session_maker) as session:
            ids_by_key = await self._chunk_ids_by_key(session, keys)
            chunk_ids = list(ids_by_key.values())
            if chunk_ids:
                params = {f"chunk_id_{index}": value for index, value in enumerate(chunk_ids)}
                placeholders = ", ".join(f":chunk_id_{index}" for index in range(len(chunk_ids)))
                await session.execute(
                    text(
                        f"DELETE FROM search_vector_embeddings WHERE chunk_id IN ({placeholders})"
                    ),
                    params,
                )
                await session.commit()

    async def delete_entity(self, entity_id: int) -> None:
        await self.initialize()
        async with db.scoped_session(self._session_maker) as session:
            await session.execute(
                text(
                    "DELETE FROM search_vector_embeddings WHERE chunk_id IN ("
                    "SELECT id FROM search_vector_chunks "
                    "WHERE project_id = :project_id AND entity_id = :entity_id)"
                ),
                {"project_id": self.scope.project_id, "entity_id": entity_id},
            )
            await session.commit()

    async def delete_orphans(self, _live_keys: Sequence[VectorKey]) -> None:
        """Remove pgvector rows absent from the current ready manifest scope."""
        await self.initialize()
        async with db.scoped_session(self._session_maker) as session:
            await session.execute(
                text(
                    "DELETE FROM search_vector_embeddings AS embeddings "
                    "WHERE embeddings.project_id = :project_id AND NOT EXISTS ("
                    "SELECT 1 FROM search_vector_chunks AS chunks "
                    "WHERE chunks.id = embeddings.chunk_id "
                    "AND chunks.project_id = :project_id "
                    "AND chunks.vector_index = 'pgvector' "
                    "AND chunks.embedding_model = :embedding_identity "
                    "AND chunks.embedding_status = 'ready')"
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
        async with db.scoped_session(self._session_maker) as session:
            result = await session.execute(
                text(
                    "SELECT c.entity_id, c.chunk_key, "
                    "1 - (e.embedding <=> CAST(:query AS vector)) AS similarity "
                    "FROM search_vector_embeddings e "
                    "JOIN search_vector_chunks c ON c.id = e.chunk_id "
                    "WHERE e.project_id = :project_id "
                    "AND e.embedding_dims = :dimensions "
                    "AND c.project_id = :project_id "
                    "AND c.vector_index = 'pgvector' "
                    "AND c.embedding_status = 'ready' "
                    "AND c.embedding_model = :embedding_identity "
                    "ORDER BY e.embedding <=> CAST(:query AS vector), "
                    "c.entity_id ASC, c.chunk_key ASC "
                    "LIMIT :limit"
                ),
                {
                    "query": self._format_vector(query),
                    "project_id": self.scope.project_id,
                    "dimensions": self.scope.dimensions,
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
                similarity=max(0.0, min(1.0, float(row["similarity"]))),
            )
            for row in result.mappings().all()
        ]
