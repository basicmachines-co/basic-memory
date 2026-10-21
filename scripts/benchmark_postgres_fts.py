"""Reproduce large-project FTS costs against a disposable local PostgreSQL database.

Example (dedicated server; do not point this at a development database):
    docker run -d --name bm-fts-bench --cpus 1 --memory 2g \
        -e POSTGRES_PASSWORD=benchmark -e POSTGRES_DB=benchmark \
        -p 127.0.0.1:55491:5432 pgvector/pgvector:pg17 \
        -c shared_buffers=128MB -c work_mem=4MB \
        -c max_parallel_workers_per_gather=0 -c jit=off
    uv run python scripts/benchmark_postgres_fts.py --seed
    uv run python scripts/benchmark_postgres_fts.py --output /tmp/fts-before.json

The default corpus has 32k documents and 320k TOAST-heavy chunks, using the actual
search DDL and query compiler. Five percent of documents match the selective query,
with its words split across chunks. No embedding model or customer data is needed.

Use --restart-container bm-fts-bench to restart the dedicated server before each
statement, then measure it twice. This clears PostgreSQL shared buffers, NOT the
Docker VM's OS page cache or Neon's file cache. Treat buffer counts as the primary
comparison; these timings do not establish cold Neon latency. Never run benchmarks
concurrently. The JSON keeps complete EXPLAIN (ANALYZE, BUFFERS) plans.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from basic_memory.models import search as ddl
from basic_memory.repository.postgres_search_query import compile_fts_filter
from basic_memory.repository.search_query import PreparedSearchQuery
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.schemas.search import SearchItemType

DEFAULT_URL = "postgresql+asyncpg://postgres:benchmark@127.0.0.1:55491/benchmark"
QUERIES = ("meeting budget review", "meeting", "archive", "missingword budget")


async def seed(engine: AsyncEngine, notes: int) -> None:
    """Create a fresh fixture; fail if the target already contains these tables."""
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE project (id integer PRIMARY KEY)"))
        await connection.execute(text("INSERT INTO project VALUES (1), (2), (3), (4)"))
        for statement in (
            ddl.CREATE_POSTGRES_SEARCH_INDEX_TABLE,
            ddl.CREATE_POSTGRES_SEARCH_INDEX_FTS_CHUNKS_TABLE,
        ):
            await connection.execute(statement)
        await connection.execute(
            text("""
                INSERT INTO search_index (
                    id, project_id, type, title, content_stems, content_snippet,
                    entity_id, file_path, permalink, metadata, created_at, updated_at
                )
                SELECT i, 1 + (i % 4), 'entity', 'Document ' || i, 'archive', 'archive',
                    i, 'notes/' || i || '.md', 'notes/' || i, '{}', now(), now()
                FROM generate_series(1, :notes) i
            """),
            {"notes": notes},
        )
        # Distinct lexemes defeat compression and force both text and vectors into
        # TOAST. Reusing 1000 vocabularies keeps seed generation manageable.
        await connection.execute(
            text("""
            CREATE TABLE filler AS
            SELECT i AS filler_id, string_agg(md5(i::text || '-' || j::text), ' ') AS body
            FROM generate_series(1,1000) i CROSS JOIN generate_series(1,128) j
            GROUP BY i
        """)
        )
    for start in range(0, notes, 1000):
        async with engine.begin() as connection:
            await connection.execute(
                text("""
                    INSERT INTO search_index_fts_chunks (
                        project_id, search_index_id, search_index_type, chunk_index, chunk_text
                    )
                    SELECT 1 + (i % 4), i, 'entity', j,
                        CASE WHEN i % 20 = 0 THEN
                            CASE j % 3 WHEN 0 THEN 'meeting '
                                WHEN 1 THEN 'budget ' ELSE 'review ' END
                            ELSE 'archive ' END || f.body
                    FROM generate_series(:start + 1, :stop) i
                    CROSS JOIN generate_series(0,9) j
                    JOIN filler f ON f.filler_id = 1 + (i % 1000)
                """),
                {"start": start, "stop": min(start + 1000, notes)},
            )
        print(f"Seeded {min(start + 1000, notes) * 10} chunks", flush=True)
    async with engine.begin() as connection:
        for statement in (
            ddl.CREATE_POSTGRES_SEARCH_INDEX_FTS,
            ddl.CREATE_POSTGRES_SEARCH_INDEX_FTS_CHUNKS_INDEX,
            ddl.CREATE_POSTGRES_SEARCH_INDEX_SCRIPT_NGRAMS_FTS,
            ddl.CREATE_POSTGRES_SEARCH_INDEX_FTS_CHUNKS_SCRIPT_NGRAMS_INDEX,
        ):
            await connection.execute(statement)
    async with engine.connect() as connection:
        await connection.execution_options(isolation_level="AUTOCOMMIT")
        await connection.execute(text("VACUUM ANALYZE"))


def restart(container: str) -> None:
    """Clear shared buffers on the explicitly named disposable benchmark server."""
    subprocess.run(["docker", "restart", container], check=True, capture_output=True)
    for _ in range(60):
        ready = subprocess.run(
            ["docker", "exec", container, "pg_isready", "-U", "postgres", "-d", "benchmark"],
            capture_output=True,
        )
        if ready.returncode == 0:
            return
        time.sleep(0.5)
    raise RuntimeError("Benchmark PostgreSQL did not restart within 30 seconds")


async def measure(engine: AsyncEngine, container: str | None) -> dict[str, Any]:
    measurements: dict[str, Any] = {}
    for query in QUERIES:
        compiled = compile_fts_filter(
            ProjectScope.of([1, 2, 3, 4]),
            PreparedSearchQuery(search_text=query, search_item_types=[SearchItemType.ENTITY]),
            allow_relaxed=True,
        )
        statements = {
            "page": (
                f"SELECT search_index.id, {compiled.score_expression} AS score "
                f"FROM {compiled.from_clause} WHERE {compiled.where_clause} "
                "ORDER BY score DESC, search_index.id ASC LIMIT 130"
            ),
            "count": (f"SELECT count(*) FROM {compiled.from_clause} WHERE {compiled.where_clause}"),
        }
        for kind, sql in statements.items():
            if container:
                await engine.dispose()
                await asyncio.to_thread(restart, container)
            for attempt in range(2):
                async with engine.begin() as connection:
                    # Match execute_fts_statement's planner setting for text queries.
                    await connection.execute(text("SET LOCAL enable_seqscan = off"))
                    await connection.execute(text("SET LOCAL statement_timeout = '120s'"))
                    result = await connection.execute(
                        text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql), compiled.params
                    )
                    explained = result.scalar_one()[0]
                label = f"{query}/{kind}/{attempt + 1}"
                measurements[label] = explained
                plan = explained["Plan"]
                print(
                    f"{label}: {explained['Execution Time']:.1f} ms; "
                    f"{plan.get('Shared Hit Blocks', 0)} hits, "
                    f"{plan.get('Shared Read Blocks', 0)} reads",
                    flush=True,
                )
    return measurements


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--notes", type=int, default=32000)
    parser.add_argument("--restart-container")
    parser.add_argument("--output", type=Path, default=Path("/tmp/postgres-fts-benchmark.json"))
    args = parser.parse_args()
    url = make_url(args.url)
    if url.host not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("Use a dedicated local PostgreSQL server")
    if args.notes <= 0:
        parser.error("--notes must be positive")
    engine = create_async_engine(url)
    try:
        if args.seed:
            await seed(engine, args.notes)
        else:
            results = await measure(engine, args.restart_container)
            args.output.write_text(json.dumps(results, indent=2) + "\n")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
