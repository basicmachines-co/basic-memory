"""Document-wide operand matching must preserve row identity and Boolean semantics."""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.repository.postgres_search_query import (
    PostgresFts,
    compile_fts_filter,
    execute_fts_statement,
)
from basic_memory.repository.search_query import PreparedSearchQuery
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.schemas.search import SearchItemType

pytestmark = pytest.mark.postgres


@pytest_asyncio.fixture
async def operand_scope(db_backend, session_maker, test_project, project_repository):
    if db_backend != "postgres":
        pytest.skip("Requires PostgreSQL")
    documents = {
        1: ("coffee", ["brewing", "decaf"]),
        2: ("coffee", ["brewing", "filter"]),
        3: ("tea", ["leaves"]),
        4: ("coffee", []),
        5: ("neutral", ["coffee", "brewing"]),
    }
    async with db.scoped_session(session_maker) as session:
        other = await project_repository.create(
            session, {"name": "other", "path": "/tmp/operand-other", "is_active": True}
        )
        for row_id, (title, chunks) in documents.items():
            await session.execute(
                text("""
                    INSERT INTO search_index(
                        id, project_id, type, title, entity_id, created_at, updated_at
                    ) VALUES (:id, :project, 'entity', :title, :id, now(), now())
                """),
                {"id": row_id, "project": test_project.id, "title": title},
            )
            for index, chunk in enumerate(chunks):
                await session.execute(
                    text("""
                        INSERT INTO search_index_fts_chunks(
                            project_id, search_index_id, search_index_type, chunk_index, chunk_text
                        ) VALUES (:project, :id, 'entity', :index, :chunk)
                    """),
                    {"project": test_project.id, "id": row_id, "index": index, "chunk": chunk},
                )
        # Neither an observation nor another project's same-numbered row may supply
        # the missing second operand to entity 4 in the requested project.
        await session.execute(
            text("""
                INSERT INTO search_index(id, project_id, type, title)
                VALUES (4, :project, 'observation', 'brewing'),
                       (4, :other, 'entity', 'brewing')
            """),
            {"project": test_project.id, "other": other.id},
        )
    return ProjectScope.single(test_project.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "relaxed", "expected"),
    [
        ("coffee brewing", False, {1, 2, 5}),
        ("coffee NOT decaf", False, {2, 4, 5}),
        ("coffee OR NOT brewing", False, {1, 2, 3, 4, 5}),
        ("NOT coffee OR NOT brewing", False, {3, 4}),
        ("NOT coffee AND NOT brewing", False, {3}),
        ("coff* brew*", False, {1, 2, 5}),
        ("coffee coffee brewing", False, {1, 2, 5}),
        ("coffee AND missingword", False, set()),
        ("coffee missingword absentterm", True, {1, 2, 4, 5}),
    ],
)
async def test_indexed_operands_preserve_document_matches(
    session_maker: async_sessionmaker[AsyncSession],
    operand_scope: ProjectScope,
    query: str,
    relaxed: bool,
    expected: set[int],
) -> None:
    fts = PostgresFts(session_maker)
    prepared = PreparedSearchQuery(search_text=query, search_item_types=[SearchItemType.ENTITY])
    rows = await fts.search(operand_scope, prepared, limit=100, offset=0, allow_relaxed=relaxed)
    assert {row.id for row in rows} == expected
    assert all(row.project_id == operand_scope.project_ids[0] for row in rows)
    assert await fts.count(operand_scope, prepared, allow_relaxed=relaxed) == len(expected)
    assert await fts.search(
        operand_scope, prepared, limit=100, offset=0, candidate_keys=[("entity", 4)]
    ) == ([] if 4 not in expected or relaxed else [row for row in rows if row.id == 4])


@pytest.mark.asyncio
async def test_operand_count_uses_gin_without_correlated_chunk_probes(
    session_maker: async_sessionmaker[AsyncSession], operand_scope: ProjectScope
) -> None:
    compiled = compile_fts_filter(
        operand_scope, PreparedSearchQuery(search_text="coffee brewing missingword")
    )
    async with db.scoped_session(session_maker) as session:
        rows = await execute_fts_statement(
            session,
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT count(*) "
            f"FROM {compiled.from_clause} WHERE {compiled.where_clause}",
            compiled.params,
            prefer_index_scans=compiled.prefer_index_scans,
            use_savepoint=True,
        )
    pending = [rows[0][0][0]["Plan"]]
    chunk_scans = []
    while pending:
        node = pending.pop()
        pending.extend(node.get("Plans", []))
        if node.get("Relation Name") == "search_index_fts_chunks":
            chunk_scans.append(node)
    assert chunk_scans
    assert all(node["Actual Loops"] <= 1 for node in chunk_scans)
    assert all(node["Node Type"] == "Bitmap Heap Scan" for node in chunk_scans)
