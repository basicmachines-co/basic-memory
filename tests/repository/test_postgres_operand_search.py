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
        # One word: in the parent only, in chunks only, prefix, negated, and absent.
        ("tea", False, {3}),
        ("decaf", False, {1}),
        ("brewing", False, {1, 2, 5}),
        ("brew*", False, {1, 2, 5}),
        ("coff*", False, {1, 2, 4, 5}),
        ("NOT coffee", False, {3}),
        ("NOT brewing", False, {3, 4}),
        ("missingword", False, set()),
        ("missingword", True, set()),
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
    assert await fts.search(
        operand_scope, prepared, limit=100, offset=0, candidate_keys=[("entity", 4)]
    ) == ([] if 4 not in expected or relaxed else [row for row in rows if row.id == 4])


@pytest.mark.parametrize("query", ["coffee", "coff*", "NOT coffee"])
def test_one_word_query_compiles_to_indexed_operands(query: str) -> None:
    """One word gathers its presence from GIN, with no correlated chunk lookup per row."""
    compiled = compile_fts_filter(ProjectScope.single(1), PreparedSearchQuery(search_text=query))
    assert "bool_or(operand = 0) AS operand_0" in compiled.from_clause
    assert "operand_1" not in compiled.from_clause
    assert "search_index.document_fts_vector @@" in compiled.where_clause
    statement = compiled.from_clause + compiled.where_clause + compiled.score_expression
    assert "operand_chunk" not in statement
    assert compiled.prefer_index_scans


@pytest.mark.asyncio
@pytest.mark.parametrize("bitmap_scans", [True, False])
@pytest.mark.parametrize("query", ["coffee brewing missingword", "brewing", "NOT brewing"])
async def test_operand_count_avoids_correlated_chunk_probes(
    session_maker: async_sessionmaker[AsyncSession],
    operand_scope: ProjectScope,
    bitmap_scans: bool,
    query: str,
) -> None:
    # "brewing" is only in chunks, so a per-row lookup would probe the chunk table
    # once for every candidate; its negation makes every scoped row a candidate.
    compiled = compile_fts_filter(operand_scope, PreparedSearchQuery(search_text=query))
    async with db.scoped_session(session_maker) as session:
        if not bitmap_scans:
            # Tiny fixtures may favor the project-key index over GIN in CI.
            # Exercise both plans while protecting the no-per-document-probe invariant.
            await session.execute(text("SET LOCAL enable_bitmapscan = off"))
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
    assert all(node["Node Type"] in {"Bitmap Heap Scan", "Index Scan"} for node in chunk_scans)


@pytest.mark.asyncio
async def test_long_query_keeps_existing_vector_path(
    session_maker: async_sessionmaker[AsyncSession], operand_scope: ProjectScope
) -> None:
    # More terms than the indexed projection budget must remain valid SQL. Include
    # a real matching term so the existing path must evaluate a candidate too.
    query = PreparedSearchQuery(
        search_text="coffee OR " + " OR ".join(f"absentword{i}" for i in range(128)),
        search_item_types=[SearchItemType.ENTITY],
    )
    compiled = compile_fts_filter(operand_scope, query)
    assert "AS operand_" not in compiled.from_clause
    fts = PostgresFts(session_maker)
    rows = await fts.search(operand_scope, query, limit=100, offset=0)
    assert {row.id for row in rows} == {1, 2, 4, 5}


@pytest_asyncio.fixture
async def ranking_scope(db_backend, session_maker, test_project):
    """Rows whose best rank comes from different places.

    The score keeps the best of the query-sized document vector, the parent vector and
    the best chunk. Repeating a word raises ts_rank, so the parent wins for 21 and a
    chunk wins for 22 and 24. 23 matches once, the score of the document vector's single
    occurrence. A prefix query also counts "coffeepot", so 25 ranks first for it and
    falls to that single-occurrence score for the exact word. 26 never matches.
    """
    if db_backend != "postgres":
        pytest.skip("Requires PostgreSQL")
    documents = {
        21: ("coffee coffee coffee coffee", ["kettle"]),
        22: ("kettle", ["coffee coffee coffee coffee coffee coffee", "coffee"]),
        23: ("coffee kettle", ["kettle"]),
        24: ("coffee coffee", ["coffee coffee coffee"]),
        25: ("kettle", ["kettle", "coffeepot coffee"]),
        26: ("kettle", ["teapot"]),
    }
    async with db.scoped_session(session_maker) as session:
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
    return ProjectScope.single(test_project.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (
            "coffee",
            [
                (25, 0.1215854),
                (22, 0.0906656),
                (21, 0.0865452),
                (24, 0.0827456),
                (23, 0.0607927),
            ],
        ),
        (
            "coff*",
            [
                (25, 0.1215854),
                (22, 0.0906656),
                (21, 0.0865452),
                (24, 0.0827456),
                (23, 0.0607927),
            ],
        ),
        (
            '"coffee"',
            [
                (22, 0.0906656),
                (21, 0.0865452),
                (24, 0.0827456),
                (23, 0.0607927),
                (25, 0.0607927),
            ],
        ),
    ],
)
async def test_one_word_ranking_keeps_order_and_scores(
    session_maker: async_sessionmaker[AsyncSession],
    ranking_scope: ProjectScope,
    query: str,
    expected: list[tuple[int, float]],
) -> None:
    # Expected scores were recorded from the single-vector path, which one-word
    # queries took before they moved to the indexed operand path.
    fts = PostgresFts(session_maker)
    prepared = PreparedSearchQuery(search_text=query, search_item_types=[SearchItemType.ENTITY])
    rows = await fts.search(ranking_scope, prepared, limit=100, offset=0)
    assert [row.id for row in rows] == [row_id for row_id, _ in expected]
    assert [row.score for row in rows] == pytest.approx([score for _, score in expected], abs=1e-6)


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["NOT coffee", "NOT coffee kettle", "kettle"])
async def test_tied_scores_sort_by_full_row_key(
    db_backend, session_maker, test_project, project_repository, query: str
) -> None:
    """Rows that tie on score and id still come back in one order, page after page.

    Every row here scores the same for each query: a negated query scores 0, and every
    title is the same plain "kettle". An entity, an observation and a relation share
    id 7 in both projects, so only type and project can order them. They are inserted
    in reverse key order, so a sort on id alone would tend to return them as stored.
    """
    if db_backend != "postgres":
        pytest.skip("Requires PostgreSQL")
    async with db.scoped_session(session_maker) as session:
        other = await project_repository.create(
            session, {"name": "tie-other", "path": "/tmp/tie-other", "is_active": True}
        )
        first, second = sorted([test_project.id, other.id])
        stored = [
            (row_id, row_type, project_id)
            for row_id in (8, 7)
            for row_type in ("relation", "observation", "entity")
            for project_id in (second, first)
        ]
        for row_id, row_type, project_id in stored:
            await session.execute(
                text("""
                    INSERT INTO search_index(
                        id, project_id, type, title, entity_id, created_at, updated_at
                    ) VALUES (:id, :project, :type, 'kettle', :id, now(), now())
                """),
                {"id": row_id, "project": project_id, "type": row_type},
            )
    scope = ProjectScope.of([first, second])
    fts = PostgresFts(session_maker)
    prepared = PreparedSearchQuery(search_text=query)

    rows = await fts.search(scope, prepared, limit=100, offset=0)
    assert [(row.id, row.type, row.project_id) for row in rows] == sorted(stored)
    assert len({row.score for row in rows}) == 1

    # Pages of a tied result must neither repeat nor skip a row.
    paged = [
        row
        for offset in range(0, len(stored), 5)
        for row in await fts.search(scope, prepared, limit=5, offset=offset)
    ]
    assert [(row.id, row.type, row.project_id) for row in paged] == sorted(stored)
