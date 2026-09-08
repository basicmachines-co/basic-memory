"""Missing identifiers remain distinct from entities that cannot supply Markdown."""

from datetime import UTC, datetime

import pytest
from fastmcp import Client

from basic_memory import db
from basic_memory.models import Entity
from basic_memory.repository.entity_repository import EntityRepository


@pytest.mark.asyncio
async def test_binary_uuid_line_read_reports_content_error(
    mcp_server, app, test_project, engine_factory
) -> None:
    _, session_maker = engine_factory
    entity_repository = EntityRepository(project_id=test_project.id)
    entity = Entity(
        title="legacy.txt",
        note_type="file",
        content_type="text/plain",
        file_path="legacy/legacy.txt",
        checksum="seeded",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.add(session, entity)

    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "read_note",
            {
                "identifier": entity.external_id,
                "project": test_project.name,
                "output_format": "json",
                "start_line": 1,
                "end_line": 10,
            },
            raise_on_error=False,
        )

    assert result.is_error
    block = result.content[0]
    assert block.type == "text"
    assert "no markdown content to slice" in block.text
    assert "NOTE_NOT_FOUND" not in block.text
    async with db.scoped_session(session_maker) as session:
        assert await entity_repository.get_by_external_id(session, entity.external_id) is not None
