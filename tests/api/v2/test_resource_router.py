"""Tests for V2 resource API routes (ID-based endpoints).

The v2 resource surface is read-only: markdown notes are written through the
knowledge router's DB-first pipeline, and every other file kind arrives
file-first through the storage-event indexing pipeline. These tests seed
entities directly (file on disk + entity row) instead of going through an API
write path.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from httpx import AsyncClient

from basic_memory.models import Project
from basic_memory import db
from basic_memory.models.knowledge import Entity
from basic_memory.repository import EntityRepository
from basic_memory.repository.note_content_repository import NoteContentRepository


@pytest.mark.asyncio
async def test_get_resource_by_id(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository: EntityRepository,
    session_maker,
):
    """Test getting file-backed resource content by external_id."""
    # Seed a non-markdown file so the read takes the file-read branch rather
    # than the accepted note-content (read-repair) path.
    test_content = "Plain text resource content."
    file_path = "test-resources/test-get.txt"
    disk_path = Path(test_project.path) / file_path
    disk_path.parent.mkdir(parents=True, exist_ok=True)
    disk_path.write_text(test_content)

    entity = Entity(
        title="test-get.txt",
        note_type="file",
        content_type="text/plain",
        file_path=file_path,
        checksum="seeded",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.add(session, entity)

    response = await client.get(f"{v2_project_url}/resource/{entity.external_id}")

    assert response.status_code == 200
    assert test_content in response.text


@pytest.mark.asyncio
async def test_get_markdown_resource_reads_accepted_note_content(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    session_maker,
):
    """Markdown resource reads should prefer accepted DB content over stale files."""
    create_response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "AcceptedResource",
            "directory": "test",
            "content": "Original file content",
        },
    )
    assert create_response.status_code == 202
    created = create_response.json()

    accepted_content = "# AcceptedResource\n\nAccepted note_content body.\n"
    repository = NoteContentRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        await repository.upsert(
            session,
            {
                "entity_id": created["id"],
                "markdown_content": accepted_content,
                "db_version": 42,
                "db_checksum": "accepted-db-checksum",
                "file_write_status": "pending",
                "last_source": "test",
            },
        )

    response = await client.get(f"{v2_project_url}/resource/{created['external_id']}")

    assert response.status_code == 200
    assert response.text == accepted_content


@pytest.mark.asyncio
async def test_get_resource_not_found(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Test getting a non-existent resource returns 404."""
    fake_uuid = "00000000-0000-0000-0000-000000000000"
    response = await client.get(f"{v2_project_url}/resource/{fake_uuid}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_resource_invalid_project_id(
    client: AsyncClient,
):
    """Test resource reads with invalid project external_id return 404."""
    fake_project_uuid = "00000000-0000-0000-0000-000000000000"
    fake_entity_uuid = "00000000-0000-0000-0000-000000000001"

    response = await client.get(f"/v2/projects/{fake_project_uuid}/resource/{fake_entity_uuid}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_v2_resource_endpoints_use_project_id_not_name(
    client: AsyncClient, test_project: Project
):
    """Verify v2 resource endpoints require project external_id UUID, not name."""
    # Try using project name instead of external_id - should fail
    fake_entity_uuid = "00000000-0000-0000-0000-000000000000"
    response = await client.get(f"/v2/projects/{test_project.name}/resource/{fake_entity_uuid}")

    # Should get 404 because name is not a valid project external_id
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_resource_write_methods_removed(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """The resource surface is read-only: POST/PUT must not be routable.

    Guards the write invariant from the 2026-07 architecture review: no API
    endpoint writes resource files inline (#1106).
    """
    fake_entity_uuid = "00000000-0000-0000-0000-000000000001"

    # No route exists at POST /resource anymore, so the path itself is gone.
    post_response = await client.post(
        f"{v2_project_url}/resource",
        json={"file_path": "test.md", "content": "test"},
    )
    assert post_response.status_code == 404

    # PUT hits the GET route's path with a disallowed method.
    put_response = await client.put(
        f"{v2_project_url}/resource/{fake_entity_uuid}",
        json={"content": "test"},
    )
    assert put_response.status_code == 405
