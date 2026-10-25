"""Title-weighted bm25 in the SQLite text search (#1851).

The text leg scored with ``bm25(search_index)`` and no column weights, so the
title counted like any body column and a note whose title was the query ranked
below notes that merely mentioned the words in their body. These tests pin the
weight table to the table's column order and prove the ranking through the real
search path.
"""

from datetime import datetime, timezone

import pytest

from basic_memory import db
from basic_memory.models import Entity
from basic_memory.models.search import CREATE_SEARCH_INDEX
from basic_memory.repository import sqlite_search_query
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_query import PreparedSearchQuery
from basic_memory.repository.search_repository import SearchIndexRow
from basic_memory.repository.search_scope import ProjectScope
from basic_memory.schemas.search import SearchItemType

TITLE = "API Documentation"
TITLE_BODY = "endpoint response request route " * 50 + " " + "文档"
MEETING_BODY = "Step 1 update the api documentation and link it from the plan 文档"
SCRIPT = "文档"


def _search_index_columns() -> list[str]:
    """The ``search_index`` columns in FTS5 declaration order."""
    ddl = str(CREATE_SEARCH_INDEX).split("fts5(", 1)[1].rsplit(")", 1)[0]
    columns: list[str] = []
    for raw in ddl.splitlines():
        line = raw.split("--", 1)[0].strip().rstrip(",")
        if not line or line.startswith(("tokenize=", "prefix=")):
            continue
        columns.append(line.split()[0])
    return columns


def test_the_weights_cover_every_search_index_column() -> None:
    columns = _search_index_columns()

    assert len(sqlite_search_query.SEARCH_INDEX_BM25_WEIGHTS) == len(columns)


def test_the_title_outweighs_the_body() -> None:
    columns = _search_index_columns()
    weights = sqlite_search_query.SEARCH_INDEX_BM25_WEIGHTS

    assert weights[columns.index("title")] > weights[columns.index("content_stems")]


def test_the_score_expression_passes_the_weights() -> None:
    compiled = sqlite_search_query.compile_fts_filter(
        ProjectScope.single(1),
        PreparedSearchQuery(search_text=TITLE),
        entity_columns=set(),
    )

    assert compiled.score_expression == sqlite_search_query.BM25_SCORE_EXPRESSION


def test_the_filtered_derived_table_uses_the_weighted_score() -> None:
    """The metadata-filtered script path ranks through a derived table."""
    compiled = sqlite_search_query.compile_fts_filter(
        ProjectScope.single(1),
        PreparedSearchQuery(search_text=f"API {SCRIPT}", metadata_filters={"status": "active"}),
        entity_columns=set(),
    )

    assert sqlite_search_query.BM25_SCORE_EXPRESSION in compiled.from_clause


async def _index_note(
    session_maker,
    search_repository,
    *,
    number: int,
    title: str,
    body: str,
    project_id: int,
    metadata: dict | None,
) -> None:
    now = datetime.now(timezone.utc)
    permalink = f"note-{number}"
    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            title=title,
            note_type="note",
            content_type="text/markdown",
            project_id=project_id,
            permalink=permalink,
            file_path=f"{permalink}.md",
            entity_metadata=metadata,
            created_at=now,
            updated_at=now,
        )
        session.add(entity)
        await session.flush()
        entity_id = entity.id

    await search_repository.index_item(
        SearchIndexRow(
            id=entity_id,
            type=SearchItemType.ENTITY.value,
            title=title,
            content_stems=body,
            content_snippet=body,
            permalink=permalink,
            file_path=f"{permalink}.md",
            entity_id=entity_id,
            metadata={"note_type": "note"},
            created_at=now,
            updated_at=now,
            project_id=project_id,
        )
    )


async def _index_title_fixture(session_maker, search_repository, project_id):
    """The shape from the issue: one titled note, 20 body matches, 100 others."""
    await _index_note(
        session_maker,
        search_repository,
        number=1,
        title=TITLE,
        body=TITLE_BODY,
        project_id=project_id,
        metadata={"status": "active"},
    )
    for number in range(2, 22):
        await _index_note(
            session_maker,
            search_repository,
            number=number,
            title=f"Meeting {number}",
            body=MEETING_BODY,
            project_id=project_id,
            metadata={"status": "active"},
        )
    for number in range(22, 122):
        await _index_note(
            session_maker,
            search_repository,
            number=number,
            title=f"Journal {number}",
            body="The weather was fine and nothing else happened.",
            project_id=project_id,
            metadata={"status": "active"},
        )


@pytest.mark.asyncio
async def test_an_exact_title_match_ranks_first(session_maker, search_repository, test_project):
    if isinstance(search_repository, PostgresSearchRepository):
        pytest.skip("the title weighting is the SQLite bm25 score")

    await _index_title_fixture(session_maker, search_repository, test_project.id)

    results = await search_repository.search(search_text=TITLE)

    assert [row.title for row in results].index(TITLE) == 0


@pytest.mark.asyncio
async def test_a_metadata_filter_does_not_change_the_ranking(
    session_maker, search_repository, test_project
):
    """The filtered script path ranks in a derived table, not inline.

    Every note in the fixture carries the filtered status, so the filter must not
    reorder anything: the same query has to answer in the same order with and
    without it. Before the derived table carried the weighted score, the filtered
    answer fell back to unweighted bm25 and drifted from the unfiltered one.
    """
    if isinstance(search_repository, PostgresSearchRepository):
        pytest.skip("the title weighting is the SQLite bm25 score")

    await _index_title_fixture(session_maker, search_repository, test_project.id)

    unfiltered = await search_repository.search(search_text=f"API {SCRIPT}")
    filtered = await search_repository.search(
        search_text=f"API {SCRIPT}",
        metadata_filters={"status": "active"},
    )

    assert [row.title for row in unfiltered]
    assert [row.title for row in filtered] == [row.title for row in unfiltered]
