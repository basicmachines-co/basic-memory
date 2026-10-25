"""Titles outrank bodies in SQLite text search (#1851).

The text leg scored with ``bm25(search_index)`` and no column weights, so the
title counted like any body column. These tests pin the weight table to the
table's column order so the two cannot drift apart.
"""

from basic_memory.models.search import CREATE_SEARCH_INDEX
from basic_memory.repository import sqlite_search_query
from basic_memory.repository.search_query import PreparedSearchQuery
from basic_memory.repository.search_scope import ProjectScope


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
        PreparedSearchQuery(search_text="API Documentation"),
        entity_columns=set(),
    )

    assert compiled.score_expression == sqlite_search_query.BM25_SCORE_EXPRESSION
