"""Experimental, approximate single-word retrieval using RUM-ranked source hits.

This is a benchmark, not the application's search implementation. Seed a disposable
local database with benchmark_postgres_fts.py, install RUM in its PostgreSQL image,
then use --create-indexes once. Run serially; keep the JSON plans for comparison.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from basic_memory.repository.postgres_search_query import compile_fts_filter, prepare_search_term
from basic_memory.repository.script_ngrams import analyze_script_query
from basic_memory.repository.search_filters import CompiledFilter
from basic_memory.repository.search_query import PreparedSearchQuery
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.schemas.search import SearchItemType


def compile_ranked_candidates(
    scope: ProjectScope, query: PreparedSearchQuery, *, budget: int
) -> CompiledFilter:
    """Rank a bounded set of parent and chunk hits, enforcing filters before limits.

    The caller must verify that PostgreSQL parses the query into one positive
    operand. Multiword/Boolean/script queries need a different retrieval design.
    Scores are the original ts_rank values of retrieved hits; omitted chunks can
    change a document's score. Counts must use the ordinary exhaustive compiler.
    """
    if not query.search_text or analyze_script_query(query.search_text).gram_phrases:
        raise ValueError("This experiment requires a word-only text query")
    if budget < 1:
        raise ValueError("Candidate budget must be positive")
    compiled = compile_fts_filter(scope, replace(query, search_text=None))
    compiled.params["text"] = prepare_search_term(query.search_text)
    conditions = compiled.where_clause
    if len(scope.project_ids) > 1:
        # RUM cannot provide ordered scans with the scalar-array project condition
        # in these measured plans. Keep scope enforced as a filter instead.
        conditions = conditions.replace(
            "search_index.project_id IN", "(search_index.project_id + 0) IN"
        )
    parent = (
        "(SELECT search_index.project_id, search_index.id, search_index.type, "
        "ts_rank(search_index.textsearchable_index_col, to_tsquery('english', :text)) AS score "
        f"FROM {compiled.from_clause} WHERE {conditions} "
        "AND search_index.textsearchable_index_col @@ to_tsquery('english', :text) "
        "ORDER BY search_index.textsearchable_index_col <=> to_tsquery('english', :text) "
        f"LIMIT {budget})"
    )
    chunk_project = scope.predicate(
        "chunks.project_id" if len(scope.project_ids) == 1 else "(chunks.project_id + 0)",
        compiled.params,
    )
    child = (
        "(SELECT chunks.project_id, chunks.search_index_id AS id, "
        "chunks.search_index_type AS type, "
        "ts_rank(chunks.textsearchable_index_col, to_tsquery('english', :text)) AS score "
        f"FROM search_index_fts_chunks chunks WHERE {chunk_project} "
        "AND chunks.textsearchable_index_col @@ to_tsquery('english', :text) "
        f"AND EXISTS (SELECT 1 FROM {compiled.from_clause} "
        "WHERE search_index.project_id = chunks.project_id "
        "AND search_index.id = chunks.search_index_id "
        "AND search_index.type = chunks.search_index_type "
        # The fence prevents the planner from reversing the lookup and probing
        # every parent's chunks. Check eligibility in ranked-hit order instead.
        f"AND {conditions} OFFSET 0) "
        "ORDER BY chunks.textsearchable_index_col <=> to_tsquery('english', :text) "
        f"LIMIT {budget * 4})"
    )
    source = (
        "(SELECT project_id, id, type, MAX(score) AS score "
        f"FROM ({parent} UNION ALL {child}) hits "
        "GROUP BY project_id, id, type OFFSET 0) search_index"
    )
    return replace(
        compiled,
        from_clause=source,
        where_clause="TRUE",
        score_expression="search_index.score",
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url", default="postgresql+asyncpg://postgres:benchmark@127.0.0.1:55491/benchmark"
    )
    parser.add_argument("--create-indexes", action="store_true")
    parser.add_argument("--project-ids", nargs="+", type=int, default=[1, 2, 3, 4])
    parser.add_argument("--queries", nargs="+", default=["meeting", "archive"])
    parser.add_argument("--budget", type=int, default=500)
    parser.add_argument("--limit", type=int, default=130)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("/tmp/postgres-ranked-candidates.json"))
    args = parser.parse_args()
    if make_url(args.url).host not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("Use a dedicated local PostgreSQL server")
    if min(args.budget, args.limit, args.repeats) < 1:
        parser.error("Budget, limit and repeats must be positive")
    engine = create_async_engine(args.url)
    results: dict[str, Any] = {}
    try:
        if args.create_indexes:
            async with engine.begin() as connection:
                await connection.execute(text("CREATE EXTENSION IF NOT EXISTS rum"))
                for name, table in (
                    ("experiment_parent_rum", "search_index"),
                    ("experiment_chunks_rum", "search_index_fts_chunks"),
                ):
                    await connection.execute(
                        text(
                            f"CREATE INDEX IF NOT EXISTS {name} ON {table} "
                            "USING rum(project_id, textsearchable_index_col rum_tsvector_ops)"
                        )
                    )
        for query_text in args.queries:
            compiled = compile_ranked_candidates(
                ProjectScope.of(args.project_ids),
                PreparedSearchQuery(
                    search_text=query_text, search_item_types=[SearchItemType.ENTITY]
                ),
                budget=args.budget,
            )
            sql = (
                "SELECT search_index.project_id, search_index.id, "
                f"{compiled.score_expression} AS score FROM {compiled.from_clause} "
                f"WHERE {compiled.where_clause} ORDER BY score DESC, search_index.id "
                f"LIMIT {args.limit}"
            )
            for attempt in range(args.repeats):
                async with engine.begin() as connection:
                    nodes = await connection.scalar(
                        text("SELECT numnode(to_tsquery('english', :text))"), compiled.params
                    )
                    if nodes != 1:
                        raise ValueError("This experiment requires one positive tsquery operand")
                    # Local to this transaction; ordinary application statements
                    # must retain their own planner settings. Bitmap plans lose
                    # RUM's ordering and evaluate every matching vector.
                    await connection.execute(text("SET LOCAL enable_seqscan = off"))
                    await connection.execute(text("SET LOCAL enable_bitmapscan = off"))
                    await connection.execute(text("SET LOCAL statement_timeout = '120s'"))
                    result = await connection.execute(
                        text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql), compiled.params
                    )
                    explained = result.scalar_one()[0]
                label = f"{query_text}/{attempt + 1}"
                results[label] = explained
                plan = explained["Plan"]
                buffers = plan.get("Shared Hit Blocks", 0) + plan.get("Shared Read Blocks", 0)
                print(
                    f"{label}: {explained['Execution Time']:.1f} ms; {buffers} buffers", flush=True
                )
                args.output.write_text(json.dumps(results, indent=2) + "\n")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
