"""Verify doctor reads existing data through the project-scoped API/client."""

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.mcp.clients.project import ProjectClient


@pytest.mark.asyncio
async def test_integrity_client_reports_existing_orphan(client, test_project, session_maker):
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "INSERT INTO search_index (project_id, id, entity_id, type) "
                "VALUES (:project, 999, 999, 'entity')"
            ),
            {"project": test_project.id},
        )
    report = await ProjectClient(client).get_integrity(test_project.external_id)
    assert report.search_rows == report.orphaned_rows == 1
    assert report.has_anomalies
    assert "999" in report.examples[0]
    assert await ProjectClient(client).get_integrity(test_project.external_id) == report


@pytest.mark.asyncio
async def test_integrity_unknown_project_is_not_healthy(client):
    response = await client.get("/v2/projects/00000000-0000-0000-0000-000000000000/integrity")
    assert response.status_code == 404
