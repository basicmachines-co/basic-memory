"""Docker-free assertions for PostgreSQL search SQL projections."""

import json
import re
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_index_row import SearchIndexRow


class _RecordingSession:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> None:
        self.executed.append((str(statement), params))


@pytest.mark.asyncio
async def test_chunk_insert_projects_every_json_recordset_column() -> None:
    """The chunk INSERT keeps its target and SELECT projections one-to-one."""
    repository = PostgresSearchRepository.__new__(PostgresSearchRepository)
    repository.project_id = 7
    session = _RecordingSession()
    row = SearchIndexRow(
        project_id=7,
        id=42,
        type="entity",
        file_path="notes/cjk.md",
        content_snippet="甲乙丙",
        created_at=datetime(2026, 6, 18, tzinfo=UTC),
        updated_at=datetime(2026, 6, 18, tzinfo=UTC),
    )

    await repository._replace_fts_chunks(cast(AsyncSession, session), [row])

    insert_sql, params = session.executed[1]
    target_match = re.search(
        r"INSERT INTO search_index_fts_chunks\s*\((.*?)\)\s*SELECT",
        insert_sql,
        re.DOTALL,
    )
    projection_match = re.search(r"\)\s*SELECT\s+(.*?)\s+FROM", insert_sql, re.DOTALL)
    assert target_match is not None
    assert projection_match is not None
    target_columns = tuple(column.strip() for column in target_match.group(1).split(","))
    projected_values = tuple(value.strip() for value in projection_match.group(1).split(","))

    assert len(target_columns) == len(projected_values)
    assert target_columns[-1] == "chunk_tokens"
    assert projected_values[-1] == "chunk.chunk_tokens"
    assert json.loads(params["chunks"])[0]["chunk_tokens"] == "甲乙 乙丙"
