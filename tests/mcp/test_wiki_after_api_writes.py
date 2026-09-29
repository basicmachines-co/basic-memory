"""Local wiki projection after accepted API writes (#1625).

Every accepted write records a journal row, and the wiki projector defers until
each replayed row is materialized. The local runtime never stamped those rows,
so one API write left the wiki `partial` forever.
"""

import pytest
from sqlalchemy import select

from basic_memory import db
from basic_memory.index.local_wiki_projection import (
    LocalWikiState,
    inspect_local_wiki_projection,
)
from basic_memory.mcp.tools import delete_note, edit_note, write_note
from basic_memory.models import AcceptedProjectNoteChange


@pytest.mark.asyncio
async def test_wiki_projects_after_api_create_edit_and_delete(app, test_project, session_maker):
    await write_note(project=test_project.name, title="Kept", directory="a", content="# Kept")
    await write_note(project=test_project.name, title="Gone", directory="a", content="# Gone")
    await edit_note(
        identifier="a/kept", operation="append", content="More.", project=test_project.name
    )
    await delete_note(identifier="a/gone", project=test_project.name)

    async with db.scoped_session(session_maker) as session:
        rows = (
            await session.execute(
                select(AcceptedProjectNoteChange).where(
                    AcceptedProjectNoteChange.project_id == test_project.id
                )
            )
        ).scalars()
        unsettled = [row.partition_position for row in rows if row.materialized_at is None]
    assert unsettled == []

    inspection = await inspect_local_wiki_projection(test_project, session_maker=session_maker)
    assert inspection.state is not LocalWikiState.partial
    assert inspection.plan.result.pending_materialization == ()
