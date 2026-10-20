"""Read-only counts of note writes that have not reached their file."""

import pytest

from basic_memory import db
from basic_memory.models import Entity, NoteContent
from basic_memory.repository.note_materialization_health import (
    UnsyncedNote,
    inspect_note_materialization,
)


async def _note(session, project_id: int, path: str, status: str, error: str | None = None):
    entity = Entity(
        project_id=project_id,
        title=path,
        note_type="note",
        content_type="text/markdown",
        file_path=path,
    )
    session.add(entity)
    await session.flush()
    session.add(
        NoteContent(
            entity_id=entity.id,
            project_id=project_id,
            external_id=f"note-{entity.id}",
            file_path=path,
            markdown_content="# note",
            db_version=1,
            db_checksum="checksum",
            file_write_status=status,
            last_materialization_error=error,
        )
    )


@pytest.mark.asyncio
async def test_counts_unsynced_notes_by_status_with_bounded_examples(session_maker, test_project):
    async with db.scoped_session(session_maker) as session:
        await _note(session, test_project.id, "synced.md", "synced")
        await _note(session, test_project.id, "pending.md", "pending")
        await _note(session, test_project.id, "writing.md", "writing")
        await _note(session, test_project.id, "b-failed.md", "failed", "disk full")
        await _note(session, test_project.id, "a-failed.md", "failed", None)
        await _note(session, test_project.id, "changed.md", "external_change_detected")
        await session.commit()

    health = await inspect_note_materialization(session_maker, test_project.id, sample_limit=1)

    assert health.in_flight == 2
    assert health.failed_total == 2
    assert health.failed == (UnsyncedNote(file_path="a-failed.md", error=None),)
    assert health.external_change_total == 1
    assert health.external_change == (UnsyncedNote(file_path="changed.md", error=None),)


@pytest.mark.asyncio
async def test_fully_synced_project_reports_nothing(session_maker, test_project):
    async with db.scoped_session(session_maker) as session:
        await _note(session, test_project.id, "synced.md", "synced")
        await session.commit()

    health = await inspect_note_materialization(session_maker, test_project.id)

    assert health.in_flight == health.failed_total == health.external_change_total == 0
    assert health.failed == health.external_change == ()
