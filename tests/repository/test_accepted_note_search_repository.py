"""Tests for accepted-note search repository operations."""

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.indexing.accepted_note_search import build_accepted_note_search_row
from basic_memory.repository.accepted_note_search_repository import (
    AcceptedNoteSearchRepository,
)


class _Dialect:
    def __init__(self, name: str) -> None:
        self.name = name


class _Bind:
    def __init__(self, dialect_name: str) -> None:
        self.dialect = _Dialect(dialect_name)


class _RecordingSession:
    def __init__(self, *, dialect_name: str = "postgresql") -> None:
        self.executed: list[tuple[str, dict[str, Any]]] = []
        self._bind = _Bind(dialect_name)

    def get_bind(self) -> _Bind:
        return self._bind

    async def execute(self, statement: Any, params: dict[str, Any]) -> None:
        self.executed.append((str(statement), params))


@pytest.mark.asyncio
async def test_refresh_entity_replaces_project_scoped_hot_search_row() -> None:
    repository = AcceptedNoteSearchRepository(project_id=7)
    session = _RecordingSession()
    created_at = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    updated_at = datetime(2026, 6, 18, 13, 0, tzinfo=UTC)
    row = build_accepted_note_search_row(
        entity_id=42,
        title="Project Plan",
        note_type="decision",
        entity_metadata={"tags": ["strategy"]},
        permalink="main/project-plan",
        file_path="notes/project-plan.md",
        search_content="Main body",
        created_at=created_at,
        updated_at=updated_at,
        project_id=7,
    )

    await repository.refresh_entity(cast(AsyncSession, session), row)

    assert len(session.executed) == 2
    delete_sql, delete_params = session.executed[0]
    insert_sql, insert_params = session.executed[1]
    assert "DELETE FROM search_index" in delete_sql
    assert delete_params == {"entity_id": 42, "project_id": 7}
    assert "CAST(:metadata AS jsonb)" in insert_sql
    assert "ON CONFLICT (permalink, project_id)" in insert_sql
    assert insert_params == {
        "id": 42,
        "title": "Project Plan",
        "content_stems": row.content_stems,
        "content_snippet": "Main body",
        "permalink": "main/project-plan",
        "file_path": "notes/project-plan.md",
        "type": "entity",
        "metadata": '{"note_type": "decision"}',
        "entity_id": 42,
        "created_at": created_at,
        "updated_at": updated_at,
        "project_id": 7,
    }


@pytest.mark.asyncio
async def test_refresh_entity_uses_plain_insert_for_sqlite_virtual_table() -> None:
    repository = AcceptedNoteSearchRepository(project_id=7)
    session = _RecordingSession(dialect_name="sqlite")
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    row = build_accepted_note_search_row(
        entity_id=42,
        title="Project Plan",
        note_type="decision",
        entity_metadata=None,
        permalink="main/project-plan",
        file_path="notes/project-plan.md",
        search_content="Main body",
        created_at=now,
        updated_at=now,
        project_id=7,
    )

    await repository.refresh_entity(cast(AsyncSession, session), row)

    insert_sql, _ = session.executed[1]
    assert "ON CONFLICT" not in insert_sql
    assert "CAST(:metadata AS jsonb)" not in insert_sql
    assert ":metadata" in insert_sql


@pytest.mark.asyncio
async def test_refresh_entity_rejects_cross_project_rows() -> None:
    repository = AcceptedNoteSearchRepository(project_id=7)
    session = _RecordingSession()
    now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
    row = build_accepted_note_search_row(
        entity_id=42,
        title="Project Plan",
        note_type="decision",
        entity_metadata=None,
        permalink="main/project-plan",
        file_path="notes/project-plan.md",
        search_content="Main body",
        created_at=now,
        updated_at=now,
        project_id=8,
    )

    with pytest.raises(ValueError, match="does not match repository project_id"):
        await repository.refresh_entity(cast(AsyncSession, session), row)

    assert session.executed == []
