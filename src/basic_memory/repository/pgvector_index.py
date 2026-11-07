"""Built-in pgvector implementation of the semantic vector index contract."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError
from basic_memory.repository.semantic_vector_index import (
    VectorAdmission,
    VectorDeletion,
    VectorIndexScope,
    VectorKey,
    VectorMatch,
    VectorRecord,
    VectorRowSearch,
    collapse_to_rows_sql,
    read_row_search,
    validate_query_dimensions,
    validate_vector_dimensions,
)


# pgvector's HNSW scan hands back at most ``hnsw.ef_search`` rows, 40 by default,
# whatever the LIMIT asks for; the server caps the setting at 1000.
HNSW_EF_SEARCH_DEFAULT = 40
HNSW_EF_SEARCH_MAX = 1000
# A filtered search ranks its admitted chunks exactly while there are at most this many,
# and uses the filtered HNSW scan above it. Measured on 300,000 clustered 384-dimension
# chunks (#1746): exact ranking took 47 ms for 30,000 admitted chunks and 600 ms for
# 270,000; the forced HNSW scan took about 100 ms at any selectivity but recalled only
# 66% of the true top 100 when 10% of chunks were admitted.
EXACT_ADMISSION_CHUNK_LIMIT = 50_000
# The most chunks one approximate HNSW scan may read before collapsing them to rows.
HNSW_MAX_CHUNK_SCAN = 50_000


@dataclass(frozen=True, slots=True)
class CandidateScan:
    """A ``candidates`` CTE and the most chunks it can read; None ranks every one."""

    cte: str
    chunk_limit: int | None


_PGVECTOR_VERSION = re.compile(r"^(\d+)\.(\d+)")


def pgvector_supports_iterative_scan(extversion: str) -> bool:
    """Whether this pgvector keeps scanning an HNSW index until the LIMIT is filled.

    Iterative index scans arrived in pgvector 0.8.0. Before that, a scan stops at
    ``hnsw.ef_search`` candidates, and a filter applied afterwards (the manifest
    join, a scope narrower than the table) leaves the window under-filled, so the
    adapter cannot promise the nearest ``limit`` rows in scope. A version string
    the pattern cannot read counts as older.
    """
    match = _PGVECTOR_VERSION.match(extversion)
    return match is not None and (int(match.group(1)), int(match.group(2))) >= (0, 8)


def hnsw_ef_search_for(limit: int) -> int:
    """The candidate-list size that lets one HNSW scan return ``limit`` rows."""
    return min(max(limit, HNSW_EF_SEARCH_DEFAULT), HNSW_EF_SEARCH_MAX)


def admission_count_sql(chunks_in_scope: str, admission: VectorAdmission) -> str:
    """Count a filter's admitted chunks, up to ``:exact_cap``, from the manifest alone.

    The count only chooses the plan, so it reads just what the admission names: scope and
    the chunk columns in ``search_vector_chunks AS c``. Joining the embeddings to apply
    the full manifest filter made Postgres hash-join both tables whole, which LIMIT cannot
    stop; on a 480k-chunk project that read 2.5 GB per filtered search (#1791). Without
    the join it is an index-only scan of the chunk location index that stops at the cap.
    Pending or stale chunks are counted too; that can only tip a search near the cap
    toward the HNSW scan, which is correct for any count.
    """
    return (
        "SELECT count(*) FROM ("
        "SELECT 1 FROM search_vector_chunks c "
        f"WHERE {chunks_in_scope} AND {admission.predicate} "
        "LIMIT :exact_cap) admitted"
    )


# Initializations of one database can run at once: a cloud API pre-warm and a worker
# touching the same tenant, or several workers starting together. CREATE ... IF NOT
# EXISTS is not safe under concurrency (the loser fails on a catalog unique index), so
# the DDL path takes this transaction lock and re-reads the catalog before creating.
VECTOR_STORAGE_DDL_LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"basic-memory-vector-storage-ddl-v1").digest()[:4], "big"
) % (2**31)


async def lock_vector_storage_ddl(session: AsyncSession) -> None:
    """Serialize vector storage DDL on this database until the transaction ends."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": VECTOR_STORAGE_DDL_LOCK_KEY},
    )


@dataclass(frozen=True, slots=True)
class PgVectorStorage:
    """The pgvector storage a database already has, read from the catalog."""

    extversion: str | None
    dimensions: int | None
    has_source_hash: bool
    has_indexes: bool

    def is_current_for(self, dimensions: int) -> bool:
        """Whether initialize has nothing to create, rebuild, or report."""
        return (
            self.extversion is not None
            and pgvector_supports_iterative_scan(self.extversion)
            and self.dimensions == dimensions
            and self.has_source_hash
            and self.has_indexes
        )


class PgVectorIndex:
    """Persist and query semantic vectors in PostgreSQL with pgvector."""

    max_chunk_scan = HNSW_MAX_CHUNK_SCAN

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        scope: VectorIndexScope,
    ) -> None:
        self._session_maker = session_maker
        self.scope = scope

    @staticmethod
    def _format_vector(vector: Sequence[float]) -> str:
        values = ",".join(f"{float(value):.12g}" for value in vector)
        return f"[{values}]"

    async def initialize(self) -> None:
        """Runtime readiness: nothing to do, because storage exists before runtime.

        Vector storage is created when the database is initialized (``create_storage``,
        called from ``PostgresSearchRepository.init_search_index``), the same moment
        migrations run. Runtime calls never issue DDL: CREATE INDEX IF NOT EXISTS takes
        a SHARE lock on the table even when the index exists, and run per job it queued
        every embedding write behind it until queries timed out.
        """

    async def create_storage(self) -> None:
        """Create or rebuild pgvector storage. Database initialization only."""
        # A catalog read takes no locks on our tables, so a database whose storage is
        # already current (every worker start after the first) runs no DDL at all.
        async with db.scoped_session(self._session_maker) as session:
            storage = await self._read_storage(session)
        if storage.is_current_for(self.scope.dimensions):
            return

        async with db.scoped_session(self._session_maker) as session:
            await lock_vector_storage_ddl(session)
            # Another initialization may have finished while this one waited.
            storage = await self._read_storage(session)
            if storage.is_current_for(self.scope.dimensions):
                return
            if storage.extversion is None:
                available = await session.execute(
                    text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
                )
                if available.scalar_one_or_none() is None:
                    raise SemanticDependenciesMissingError(
                        "pgvector extension is unavailable for this Postgres database."
                    )
                await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            version = await session.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            extversion = str(version.scalar_one())
            # Trigger: the installed pgvector predates iterative index scans.
            # Why: without them a scoped query silently returns fewer rows than
            #   the window asked for; a deployment gap should read as one.
            # Outcome: a typed dependency error the API reports as a bad request.
            if not pgvector_supports_iterative_scan(extversion):
                raise SemanticDependenciesMissingError(
                    f"pgvector {extversion} predates iterative index scans; semantic "
                    "search needs pgvector 0.8 or later (ALTER EXTENSION vector UPDATE)."
                )

            # Judge the existing table from the schema-local catalog read above, so a
            # same-named table elsewhere on the search_path is never rebuilt or dropped.
            existing_dimensions = storage.dimensions
            storage_missing = existing_dimensions is None
            source_hash_missing = existing_dimensions is not None and not storage.has_source_hash
            dimensions_changed = (
                existing_dimensions is not None and existing_dimensions != self.scope.dimensions
            )
            if dimensions_changed or source_hash_missing:
                logger.warning(
                    "Vector storage schema mismatch: table dimensions={existing}, "
                    "provider dimensions={expected}, source_hash_missing={source_hash_missing}. "
                    "Recreating vector storage.",
                    existing=existing_dimensions,
                    expected=self.scope.dimensions,
                    source_hash_missing=source_hash_missing,
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
                        source_hash TEXT NOT NULL,
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

            # Trigger: pgvector storage was created or its fixed-width column changed.
            # Why: SQL manifest rows can otherwise remain `ready` after their vectors
            # disappeared, causing incremental sync to skip them forever.
            # Outcome: the normal sync pipeline re-embeds every affected chunk.
            if storage_missing or dimensions_changed or source_hash_missing:
                await session.execute(
                    text(
                        "UPDATE search_vector_chunks SET embedding_status = 'pending' "
                        "WHERE vector_index = 'pgvector'"
                    )
                )
            await session.commit()

    async def _read_storage(self, session: AsyncSession) -> PgVectorStorage:
        # Catalog reads take no locks on our tables. Only objects in the schema that
        # unqualified DDL and writes target count: a match elsewhere on the search_path
        # (say, public under a tenant schema) must not skip creating the local tables.
        result = await session.execute(
            text(
                """
                WITH local AS (
                    SELECT oid, relname FROM pg_class
                    WHERE relnamespace = current_schema()::regnamespace
                      AND relname IN (
                          'search_vector_embeddings',
                          'idx_search_vector_embeddings_project_dims',
                          'idx_search_vector_embeddings_hnsw'
                      )
                ),
                embeddings AS (
                    SELECT a.attname, a.atttypmod
                    FROM pg_attribute a JOIN local ON a.attrelid = local.oid
                    WHERE local.relname = 'search_vector_embeddings' AND NOT a.attisdropped
                )
                SELECT
                    (SELECT extversion FROM pg_extension WHERE extname = 'vector')
                        AS extversion,
                    (SELECT atttypmod FROM embeddings WHERE attname = 'embedding')
                        AS dimensions,
                    EXISTS (SELECT 1 FROM embeddings WHERE attname = 'source_hash')
                        AS has_source_hash,
                    (SELECT count(*) FROM local WHERE relname LIKE 'idx_%') = 2
                        AS has_indexes
                """
            )
        )
        row = result.mappings().one()
        return PgVectorStorage(
            extversion=None if row["extversion"] is None else str(row["extversion"]),
            dimensions=None if row["dimensions"] is None else int(row["dimensions"]),
            has_source_hash=bool(row["has_source_hash"]),
            has_indexes=bool(row["has_indexes"]),
        )

    async def upsert(self, project_id: int, records: Sequence[VectorRecord]) -> None:
        if not records:
            return
        validate_vector_dimensions(self.scope, records)

        async with db.scoped_session(self._session_maker) as session:
            keys = [record.key for record in records]
            params: dict[str, object] = {"project_id": project_id}
            predicates: list[str] = []
            for index, key in enumerate(keys):
                params[f"entity_id_{index}"] = key.entity_id
                params[f"chunk_key_{index}"] = key.chunk_key
                predicates.append(
                    f"(entity_id = :entity_id_{index} AND chunk_key = :chunk_key_{index})"
                )
            result = await session.execute(
                text(
                    "SELECT id, entity_id, chunk_key, source_hash "
                    "FROM search_vector_chunks "
                    "WHERE project_id = :project_id AND ("
                    + " OR ".join(predicates)
                    + ") FOR UPDATE"
                ),
                params,
            )
            manifest_by_key = {
                VectorKey(
                    entity_id=int(row["entity_id"]),
                    chunk_key=str(row["chunk_key"]),
                ): (int(row["id"]), str(row["source_hash"]))
                for row in result.mappings().all()
            }
            missing = [key for key in keys if key not in manifest_by_key]
            if missing:
                raise RuntimeError(f"Vector manifest rows are missing for keys: {missing!r}")

            current_records = [
                record for record in records if manifest_by_key[record.key][1] == record.source_hash
            ]
            if not current_records:
                return

            params = {"project_id": project_id}
            values: list[str] = []
            for index, record in enumerate(current_records):
                params[f"chunk_id_{index}"] = manifest_by_key[record.key][0]
                params[f"embedding_{index}"] = self._format_vector(record.values)
                params[f"dimensions_{index}"] = len(record.values)
                params[f"source_hash_{index}"] = record.source_hash
                values.append(
                    f"(:chunk_id_{index}, :project_id, "
                    f"CAST(:embedding_{index} AS vector), :dimensions_{index}, "
                    f":source_hash_{index}, NOW())"
                )
            await session.execute(
                text(f"""
                    INSERT INTO search_vector_embeddings (
                        chunk_id, project_id, embedding, embedding_dims, source_hash, updated_at
                    ) VALUES {", ".join(values)}
                    ON CONFLICT (chunk_id) DO UPDATE SET
                        project_id = EXCLUDED.project_id,
                        embedding = EXCLUDED.embedding,
                        embedding_dims = EXCLUDED.embedding_dims,
                        source_hash = EXCLUDED.source_hash,
                        updated_at = NOW()
                """),
                params,
            )
            await session.commit()

    async def delete(self, project_id: int, records: Sequence[VectorDeletion]) -> None:
        if not records:
            return
        async with db.scoped_session(self._session_maker) as session:
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
                    "SELECT id, entity_id, chunk_key FROM search_vector_chunks "
                    "WHERE project_id = :project_id AND ("
                    + " OR ".join(predicates)
                    + ") FOR UPDATE"
                ),
                params,
            )
            chunk_ids = [int(row["id"]) for row in result.mappings().all()]
            if chunk_ids:
                params = {f"chunk_id_{index}": value for index, value in enumerate(chunk_ids)}
                placeholders = ", ".join(f":chunk_id_{index}" for index in range(len(chunk_ids)))
                await session.execute(
                    text(
                        f"DELETE FROM search_vector_embeddings WHERE chunk_id IN ({placeholders})"
                    ),
                    params,
                )
                await session.execute(
                    text(f"DELETE FROM search_vector_chunks WHERE id IN ({placeholders})"),
                    params,
                )
                await session.commit()

    async def delete_entity(self, project_id: int, entity_id: int) -> None:
        async with db.scoped_session(self._session_maker) as session:
            await session.execute(
                text(
                    "DELETE FROM search_vector_embeddings WHERE chunk_id IN ("
                    "SELECT id FROM search_vector_chunks "
                    "WHERE project_id = :project_id AND entity_id = :entity_id)"
                ),
                {"project_id": project_id, "entity_id": entity_id},
            )
            await session.commit()

    async def delete_orphans(self, project_id: int, _live_keys: Sequence[VectorKey]) -> None:
        """Remove pgvector rows absent from one project's current ready manifest."""
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
                    "AND chunks.source_hash = embeddings.source_hash "
                    "AND chunks.embedding_status = 'ready')"
                ),
                {
                    "project_id": project_id,
                    "embedding_identity": self.scope.embedding_identity,
                },
            )
            await session.commit()

    async def search(
        self,
        query: Sequence[float],
        *,
        limit: int,
        projects: ProjectScope,
        admission: VectorAdmission | None = None,
    ) -> list[VectorMatch]:
        if not query or limit <= 0 or projects.is_empty:
            return []
        params: dict[str, object] = {"limit": limit}
        async with db.scoped_session(self._session_maker) as session:
            scan = await self._candidates(
                session, query, params, projects, admission, chunk_scan=limit
            )
            result = await session.execute(
                text(
                    f"WITH {scan.cte} "
                    "SELECT entity_id, chunk_key, 1 - distance AS similarity "
                    "FROM candidates "
                    "ORDER BY distance ASC, entity_id ASC, chunk_key ASC "
                    "LIMIT :limit"
                ),
                params,
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

    async def search_rows(
        self,
        query: Sequence[float],
        *,
        limit: int,
        chunk_scan: int,
        projects: ProjectScope,
        admission: VectorAdmission | None = None,
        chunks_per_row: int = 1,
    ) -> VectorRowSearch:
        if not query or limit <= 0 or projects.is_empty:
            return VectorRowSearch(matches=[], truncated=False)
        params: dict[str, object] = {"row_limit": limit, "chunks_per_row": chunks_per_row}
        async with db.scoped_session(self._session_maker) as session:
            scan = await self._candidates(
                session,
                query,
                params,
                projects,
                admission,
                chunk_scan=min(chunk_scan, HNSW_MAX_CHUNK_SCAN),
            )
            result = await session.execute(text(collapse_to_rows_sql(scan.cte)), params)
        return read_row_search(
            result.mappings().all(),
            similarity=lambda distance: max(0.0, min(1.0, 1.0 - distance)),
            chunk_limit=scan.chunk_limit,
        )

    async def _candidates(
        self,
        session: AsyncSession,
        query: Sequence[float],
        params: dict[str, object],
        projects: ProjectScope,
        admission: VectorAdmission | None,
        *,
        chunk_scan: int,
    ) -> CandidateScan:
        """Prepare ``session`` and return a ``candidates`` CTE of ranked ready chunks.

        Binds its own names into ``params``. The CTE's columns are those
        ``collapse_to_rows_sql`` reads; ``distance`` is the cosine distance.
        """
        validate_query_dimensions(self.scope, query)
        params.update(
            {
                "query": self._format_vector(query),
                "dimensions": self.scope.dimensions,
                "embedding_identity": self.scope.embedding_identity,
                "chunk_scan": chunk_scan,
            }
        )
        # The scope binds its ids once; both predicates reference the same names.
        embeddings_in_scope = projects.predicate("e.project_id", params)
        chunks_in_scope = projects.predicate("c.project_id", params)
        manifest = (
            f"{embeddings_in_scope} "
            "AND e.embedding_dims = :dimensions "
            f"AND {chunks_in_scope} "
            "AND c.vector_index = 'pgvector' "
            "AND c.embedding_status = 'ready' "
            "AND c.embedding_model = :embedding_identity "
            "AND e.source_hash = c.source_hash"
        )
        admitted = ""
        if admission is not None:
            params.update(admission.params)
            admitted = f" AND {admission.predicate}"
        columns = (
            "c.entity_id, c.chunk_key, c.project_id, c.source_type, c.source_row_id, "
            "e.embedding <=> CAST(:query AS vector) AS distance "
            "FROM search_vector_embeddings e "
            "JOIN search_vector_chunks c ON c.id = e.chunk_id "
            f"WHERE {manifest}{admitted}"
        )

        # --- Choosing the scan for a filtered search (#1746) ---
        # Trigger: the search carries structured filters.
        # Why: neither plan suits every filter. An HNSW scan that filters as it goes
        #     is fast when most chunks pass, but a selective filter makes it give up
        #     before it has the window (recall 0.66 at 10% admitted) or scan for long.
        #     An exact ranking of just the admitted chunks is always complete and is
        #     fast while they are few (47 ms for 30,000), slow when they are many
        #     (600 ms for 270,000). The planner cannot pick: it underestimates the
        #     filter's rows and chose the exact plan even at 90% admitted.
        # Outcome: count the admitted chunks, stopping at the limit. Few: rank them
        #     exactly. Many: force the HNSW scan, which then needs only a short
        #     iterative walk because most of what it visits passes.
        if admission is not None:
            counted = await session.execute(
                text(admission_count_sql(chunks_in_scope, admission)),
                {**params, "exact_cap": EXACT_ADMISSION_CHUNK_LIMIT + 1},
            )
            if int(counted.scalar_one()) <= EXACT_ADMISSION_CHUNK_LIMIT:
                # No ORDER BY on the distance, so the HNSW index cannot serve this
                # scan: Postgres computes every admitted chunk's distance, and the
                # caller ranks them.
                return CandidateScan(
                    cte=f"candidates AS MATERIALIZED (SELECT {columns})", chunk_limit=None
                )

        # Both settings are transaction-local, so they last exactly as long as the
        # caller's scoped session. The scan is sized to the chunks it must read and
        # continues past that until the scope and manifest filters have admitted
        # enough, instead of stopping at the first ef_search candidates.
        await session.execute(
            text("SELECT set_config('hnsw.ef_search', :ef_search, true)"),
            {"ef_search": str(hnsw_ef_search_for(chunk_scan))},
        )
        await session.execute(
            text("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true)")
        )
        if admission is not None:
            # The only plan without a sort is the ordered HNSW scan; disabling sorts
            # keeps the misestimated filter from choosing the exact plan.
            await session.execute(text("SELECT set_config('enable_sort', 'off', true)"))
        # A relaxed iterative scan may hand rows back slightly out of distance order,
        # so the scan takes its chunks by distance alone and the caller sorts them.
        return CandidateScan(
            cte=(
                f"candidates AS MATERIALIZED (SELECT {columns} "
                "ORDER BY e.embedding <=> CAST(:query AS vector) LIMIT :chunk_scan)"
            ),
            chunk_limit=chunk_scan,
        )
