"""Read-only, database-scoped search over an explicit effective project set.

Shared-storage sqlite-vec and pgvector support one vector query. Project-isolated
external indexes (including Milvus) remain supported by the single-project API;
this reader rejects them for vector/hybrid retrieval without opening an adapter.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import logfire
from sqlalchemy import Result, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.postgres_search_query import PostgresSearchQuery
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.script_ngrams import analyze_script_query
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_query import PreparedSearchQuery
from basic_memory.repository.search_repository_base import FUSION_BONUS, TOP_CHUNKS_PER_RESULT
from basic_memory.repository.semantic_errors import (
    SemanticDependenciesMissingError,
    SemanticSearchDisabledError,
)
from basic_memory.repository.semantic_vector_index_factory import semantic_embedding_identity
from basic_memory.repository.sqlite_search_query import SQLITE_WORD_COLUMNS, SQLiteSearchQuery
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.schemas.search import SearchRetrievalMode


# Exclude backend index columns and content_stems from page hydration.
_RESULT_COLUMNS = (
    "project_id",
    "id",
    "title",
    "permalink",
    "file_path",
    "type",
    "metadata",
    "from_id",
    "to_id",
    "relation_type",
    "entity_id",
    "content_snippet",
    "category",
    "created_at",
    "updated_at",
)


class UnsupportedMultiProjectVectorIndexError(ValueError):
    """The configured adapter cannot retrieve an explicit project set in one query."""


class MultiProjectSearchRepository:
    """Retrieve and rank one database scope; never initialize or mutate its indexes."""

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        project_ids: Sequence[int],
        *,
        app_config: BasicMemoryConfig,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        # tuple(None) raises instead of interpreting absence as unrestricted access.
        ids = tuple(project_ids)
        if any(type(project_id) is not int or project_id <= 0 for project_id in ids):
            raise ValueError("Project IDs must be positive integers")
        self.project_ids = tuple(sorted(set(ids)))
        self.session_maker = session_maker
        self.config = app_config
        self.provider = embedding_provider
        self.postgres = app_config.database_backend == DatabaseBackend.POSTGRES
        self.compiler = (
            PostgresSearchQuery(session_maker, self.project_ids)
            if self.postgres
            else SQLiteSearchQuery(session_maker, self.project_ids)
        )

    async def _parts(
        self, query: PreparedSearchQuery, *, lexical: bool
    ) -> tuple[str, str, dict[str, Any], str, str]:
        # Filters are shared with the project API. Vector candidates are restricted
        # before ranking, so disallowed projects/rows never occupy a top-k window.
        return await self.compiler._build_fts_query_parts(
            search_text=query.search_text if lexical else None,
            permalink=query.permalink,
            permalink_match=query.permalink_match,
            title=query.title,
            note_types=query.note_types,
            after_date=query.after_date,
            search_item_types=query.search_item_types,
            categories=query.categories,
            metadata_filters=query.metadata_filters,
            file_path_prefix=query.file_path_prefix,
            temporal=query.temporal,
        )

    async def _fts_ctes(self, query: PreparedSearchQuery) -> tuple[str, dict[str, Any], str]:
        source, where, params, order, score = await self._parts(query, lexical=True)
        columns = ", ".join(f"search_index.{name}" for name in _RESULT_COLUMNS)
        selection = f"SELECT {columns}, {score} AS score FROM {source} WHERE {where}"
        params["fts_enabled"] = True
        selection += " AND :fts_enabled"
        ctes = f"fts_strict AS MATERIALIZED ({selection})"
        word_text = (
            analyze_script_query(query.search_text.strip()).word_text if query.search_text else None
        )
        if isinstance(self.compiler, PostgresSearchQuery):
            relaxed = self.compiler._relaxed_tsquery_text(word_text)
        else:
            relaxed = self.compiler._relaxed_fts_text(word_text)
            if relaxed and "script_text" in params:
                relaxed = f"{SQLITE_WORD_COLUMNS}: ({relaxed})"
        if relaxed and params.get("text"):
            # Decide relaxation over the complete scope, before pagination. A deep
            # empty page must never switch to a different candidate set.
            params["relaxed_text"] = relaxed
            relaxed_selection = re.sub(r":text\b", ":relaxed_text", selection)
            ctes += f""", fts AS MATERIALIZED (
                SELECT * FROM fts_strict UNION ALL
                {relaxed_selection} AND NOT EXISTS (SELECT 1 FROM fts_strict)
            )"""
        else:
            ctes += ", fts AS MATERIALIZED (SELECT * FROM fts_strict)"
        return ctes, params, order.replace("search_index.", "")

    async def _execute(
        self, session: AsyncSession, sql: str, params: dict[str, Any]
    ) -> Result[Any]:
        try:
            if not self.postgres or "fts_enabled" not in params:
                return await session.execute(text(sql), params)
            # A PostgreSQL syntax failure aborts its transaction. The savepoint
            # keeps the same connection usable for the guarded lexical retry.
            async with session.begin_nested():
                return await session.execute(text(sql), params)
        except DBAPIError as exc:
            syntax_error = (
                PostgresSearchRepository._is_tsquery_syntax_error(exc)
                if self.postgres
                else SQLiteSearchRepository._is_fts5_syntax_error(exc)
            )
            if not syntax_error:
                raise
            retry = dict(params)
            if self.postgres and params.get("relaxed_text"):
                retry["text"] = params["relaxed_text"]
            else:
                # Preserve the established empty lexical channel for invalid
                # explicit syntax; a hybrid request still retains vector matches.
                retry.update(fts_enabled=False, text="" if self.postgres else '""')
                if "title_text" in retry:
                    retry["title_text"] = retry["text"]
            return await session.execute(text(sql), retry)

    async def count(self, query: PreparedSearchQuery) -> int:
        if query.retrieval_mode != SearchRetrievalMode.FTS:
            raise ValueError("Exact counts are only supported for full-text search retrieval.")
        if not self.project_ids:
            return 0
        ctes, params, _ = await self._fts_ctes(query)
        async with db.scoped_session(self.session_maker) as session:
            result = await self._execute(session, f"WITH {ctes} SELECT COUNT(*) FROM fts", params)
            return int(result.scalar_one())

    async def search(
        self, query: PreparedSearchQuery, *, limit: int = 10, offset: int = 0
    ) -> list[SearchIndexRow]:
        if limit <= 0 or offset < 0:
            raise ValueError("Search limit must be positive and offset must be nonnegative")
        if not self.project_ids:
            return []
        mode = query.retrieval_mode
        if mode == SearchRetrievalMode.FTS:
            ctes, params, order = await self._fts_ctes(query)
            direction = "DESC" if self.postgres else "ASC"
            sql = f"""
                WITH {ctes} SELECT * FROM fts
                ORDER BY score {direction} {order}, project_id, type, id
                LIMIT :limit OFFSET :offset
            """
            params.update(limit=limit, offset=offset)
            async with db.scoped_session(self.session_maker) as session:
                with logfire.span("search.fts", project_count=len(self.project_ids)):
                    result = await self._execute(session, sql, params)
                return [SearchIndexRow.from_mapping(row._asdict()) for row in result]

        expected_index = "pgvector" if self.postgres else "sqlite-vec"
        configured_index = self.config.semantic_vector_index if self.postgres else "sqlite-vec"
        if configured_index != expected_index:
            raise UnsupportedMultiProjectVectorIndexError(
                f"Database-scoped vector/hybrid search does not support adapter '{configured_index}'. "
                "Use pgvector or sqlite-vec, or request FTS. Single-project search remains supported."
            )
        if not self.config.semantic_search_enabled or self.provider is None:
            raise SemanticSearchDisabledError("Semantic search is disabled")
        if not query.search_text or not query.search_text.strip():
            raise ValueError("Vector/hybrid search requires nonempty text")

        with logfire.span("search.embed_query", project_count=len(self.project_ids)):
            embedding = await self.provider.embed_query(query.search_text.strip())
        if len(embedding) != self.provider.dimensions:
            raise ValueError("Query dimensions do not match the configured embedding provider")

        source, where, params, _, _ = await self._parts(query, lexical=False)
        params.update(
            query_vector=json.dumps(embedding),
            embedding_model=semantic_embedding_identity(self.provider),
            vector_index=expected_index,
            dimensions=self.provider.dimensions,
            min_similarity=query.min_similarity
            if query.min_similarity is not None
            else self.config.semantic_min_similarity,
            limit=limit,
            offset=offset,
            top_chunks=TOP_CHUNKS_PER_RESULT,
        )
        distance = (
            "e.embedding <=> CAST(:query_vector AS vector)"
            if self.postgres
            else "vec_distance_L2(e.embedding, :query_vector)"
        )
        similarity = (
            f"1 - ({distance})" if self.postgres else f"1 - ({distance}) * ({distance}) / 2.0"
        )
        vector_join = (
            "e.chunk_id = c.id AND e.project_id = c.project_id"
            if self.postgres
            else "e.rowid = c.id"
        )
        dimension_check = (
            "e.embedding_dims = :dimensions"
            if self.postgres
            else "vec_length(e.embedding) = :dimensions"
        )
        # Rank the complete eligible set in SQL before paginating. Unlike a page-sized
        # candidate pool, this keeps fusion membership/normalization stable on deep pages.
        # Only page rows and their top chunks cross the database boundary.
        ctes = f"""
            eligible AS MATERIALIZED (
                SELECT search_index.project_id, search_index.type, search_index.id,
                       search_index.entity_id FROM {source} WHERE {where}
            ),
            vector_chunks AS MATERIALIZED (
                SELECT eligible.project_id, eligible.type, eligible.id,
                       c.chunk_key, c.chunk_text, {similarity} AS similarity
                FROM eligible
                JOIN search_vector_chunks c ON c.project_id = eligible.project_id
                    AND c.entity_id = eligible.entity_id
                    AND c.chunk_key LIKE eligible.type || ':' || CAST(eligible.id AS TEXT) || ':%'
                JOIN search_vector_embeddings e ON {vector_join}
                WHERE c.embedding_status = 'ready' AND c.vector_index = :vector_index
                    AND c.embedding_model = :embedding_model AND {dimension_check}
                    AND e.source_hash = c.source_hash
            ),
            vector_scores AS (
                SELECT project_id, type, id,
                       CASE WHEN MAX(similarity) > 1 THEN 1 ELSE MAX(similarity) END AS score
                FROM vector_chunks WHERE similarity >= :min_similarity
                GROUP BY project_id, type, id
            )
        """
        ranking = "SELECT project_id, type, id, score FROM vector_scores"
        if mode == SearchRetrievalMode.HYBRID:
            fts_ctes, fts_params, _ = await self._fts_ctes(query)
            params.update(fts_params)
            ctes += f""",
                {fts_ctes},
                channels AS (
                    SELECT project_id, type, id, score AS vector_score, 0.0 AS fts_score
                    FROM vector_scores
                    UNION ALL
                    SELECT project_id, type, id, 0.0,
                           COALESCE(ABS(score) / NULLIF(MAX(ABS(score)) OVER (), 0), 0) FROM fts
                )
            """
            params["fusion_bonus"] = FUSION_BONUS
            ranking = """
                SELECT project_id, type, id,
                    CASE WHEN MAX(vector_score) > MAX(fts_score)
                         THEN MAX(vector_score) + :fusion_bonus * MAX(fts_score)
                         ELSE MAX(fts_score) + :fusion_bonus * MAX(vector_score) END AS score
                FROM channels GROUP BY project_id, type, id
            """
        page_columns = ", ".join(f"s.{name}" for name in _RESULT_COLUMNS)
        sql = f"""
            WITH {ctes}, ranked AS ({ranking}),
            page AS MATERIALIZED (
                SELECT * FROM ranked ORDER BY score DESC, project_id, type, id
                LIMIT :limit OFFSET :offset
            ),
            page_chunks AS (
                SELECT c.*, ROW_NUMBER() OVER (
                    PARTITION BY c.project_id, c.type, c.id
                    ORDER BY c.similarity DESC, c.chunk_key
                ) AS chunk_rank
                FROM vector_chunks c JOIN page p
                  ON (c.project_id, c.type, c.id) = (p.project_id, p.type, p.id)
            )
            SELECT {page_columns}, p.score, c.chunk_text
            FROM page p JOIN search_index s
                ON (s.project_id, s.type, s.id) = (p.project_id, p.type, p.id)
            LEFT JOIN page_chunks c
                ON (c.project_id, c.type, c.id) = (p.project_id, p.type, p.id)
                AND c.chunk_rank <= :top_chunks
            ORDER BY p.score DESC, p.project_id, p.type, p.id, c.chunk_rank
        """
        async with db.scoped_session(self.session_maker) as session:
            if not self.postgres:
                # Connection setup only: this reader never calls adapter.initialize(),
                # which may recreate storage and invalidate manifests on schema mismatch.
                try:
                    import sqlite_vec
                except ImportError as exc:
                    raise SemanticDependenciesMissingError(
                        "sqlite-vec package is missing. Install/update basic-memory."
                    ) from exc

                connection = await session.connection()
                raw = await connection.get_raw_connection()
                driver = raw.driver_connection
                assert driver is not None
                try:
                    await driver.enable_load_extension(True)
                except AttributeError as exc:
                    raise SemanticDependenciesMissingError(
                        "This Python build does not support SQLite extension loading. "
                        "Use a Python build with extension support or request FTS."
                    ) from exc
                try:
                    await driver.load_extension(sqlite_vec.loadable_path())
                finally:
                    await driver.enable_load_extension(False)
            with logfire.span(
                "search.vector_query",
                project_count=len(self.project_ids),
                retrieval_mode=mode.value,
            ):
                result = await self._execute(session, sql, params)
            rows: dict[tuple[int, str, int], SearchIndexRow] = {}
            chunks: dict[tuple[int, str, int], list[str]] = {}
            for record in result:
                mapping = record._asdict()
                row = SearchIndexRow.from_mapping(mapping)
                key = (row.project_id, row.type, row.id)
                rows.setdefault(key, row)
                if mapping["chunk_text"] is not None:
                    chunks.setdefault(key, []).append(mapping["chunk_text"])
        return [
            replace(row, matched_chunk_text="\n---\n".join(chunks[key]) if key in chunks else None)
            for key, row in rows.items()
        ]
