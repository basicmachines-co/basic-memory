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


# --- HTTP Range support (SPEC-47 / #1403) ---

_RANGE_CONTENT = "0123456789abcdefghij"  # 20 bytes, every offset addressable


async def _seed_range_file(
    test_project: Project,
    entity_repository: EntityRepository,
    session_maker,
) -> str:
    """Seed a plain-text file entity whose bytes are position-addressable."""
    file_path = "test-resources/range.txt"
    disk_path = Path(test_project.path) / file_path
    disk_path.parent.mkdir(parents=True, exist_ok=True)
    disk_path.write_text(_RANGE_CONTENT)

    entity = Entity(
        title="range.txt",
        note_type="file",
        content_type="text/plain",
        file_path=file_path,
        checksum="seeded-range",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.add(session, entity)
    return entity.external_id


@pytest.mark.asyncio
async def test_resource_without_range_serves_full_body_with_accept_ranges(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository: EntityRepository,
    session_maker,
):
    external_id = await _seed_range_file(test_project, entity_repository, session_maker)

    response = await client.get(f"{v2_project_url}/resource/{external_id}")

    assert response.status_code == 200
    assert response.text == _RANGE_CONTENT
    assert response.headers["accept-ranges"] == "bytes"


@pytest.mark.asyncio
async def test_resource_bounded_range_returns_206_with_content_range(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository: EntityRepository,
    session_maker,
):
    external_id = await _seed_range_file(test_project, entity_repository, session_maker)

    response = await client.get(
        f"{v2_project_url}/resource/{external_id}",
        headers={"Range": "bytes=0-4"},
    )

    assert response.status_code == 206
    assert response.text == "01234"
    assert response.headers["content-range"] == "bytes 0-4/20"
    assert response.headers["accept-ranges"] == "bytes"


@pytest.mark.asyncio
async def test_resource_open_ended_range_runs_to_end(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository: EntityRepository,
    session_maker,
):
    external_id = await _seed_range_file(test_project, entity_repository, session_maker)

    response = await client.get(
        f"{v2_project_url}/resource/{external_id}",
        headers={"Range": "bytes=15-"},
    )

    assert response.status_code == 206
    assert response.text == "fghij"
    assert response.headers["content-range"] == "bytes 15-19/20"


@pytest.mark.asyncio
async def test_resource_range_end_clamps_to_body_length(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository: EntityRepository,
    session_maker,
):
    external_id = await _seed_range_file(test_project, entity_repository, session_maker)

    response = await client.get(
        f"{v2_project_url}/resource/{external_id}",
        headers={"Range": "bytes=10-9999"},
    )

    assert response.status_code == 206
    assert response.text == "abcdefghij"
    assert response.headers["content-range"] == "bytes 10-19/20"


@pytest.mark.asyncio
async def test_resource_suffix_range_serves_final_bytes(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository: EntityRepository,
    session_maker,
):
    external_id = await _seed_range_file(test_project, entity_repository, session_maker)

    response = await client.get(
        f"{v2_project_url}/resource/{external_id}",
        headers={"Range": "bytes=-4"},
    )

    assert response.status_code == 206
    assert response.text == "ghij"
    assert response.headers["content-range"] == "bytes 16-19/20"


@pytest.mark.asyncio
async def test_resource_oversized_suffix_serves_whole_body_as_206(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository: EntityRepository,
    session_maker,
):
    external_id = await _seed_range_file(test_project, entity_repository, session_maker)

    response = await client.get(
        f"{v2_project_url}/resource/{external_id}",
        headers={"Range": "bytes=-500"},
    )

    assert response.status_code == 206
    assert response.text == _RANGE_CONTENT
    assert response.headers["content-range"] == "bytes 0-19/20"


@pytest.mark.asyncio
@pytest.mark.parametrize("range_header", ["bytes=20-", "bytes=99-100", "bytes=-0"])
async def test_resource_unsatisfiable_range_returns_416(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository: EntityRepository,
    session_maker,
    range_header: str,
):
    external_id = await _seed_range_file(test_project, entity_repository, session_maker)

    response = await client.get(
        f"{v2_project_url}/resource/{external_id}",
        headers={"Range": range_header},
    )

    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */20"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "range_header",
    [
        "bytes=0-1,3-4",  # multi-range: ignored per RFC 9110
        "items=0-4",  # non-bytes unit
        "bytes=abc-def",  # malformed bounds
        "bytes=5-2",  # inverted bounds
        "bytes",  # no '=' separator
        "bytes=5",  # no '-' in the range spec
        "bytes=-",  # empty suffix
    ],
)
async def test_resource_unsupported_range_forms_serve_full_body(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_repository: EntityRepository,
    session_maker,
    range_header: str,
):
    external_id = await _seed_range_file(test_project, entity_repository, session_maker)

    response = await client.get(
        f"{v2_project_url}/resource/{external_id}",
        headers={"Range": range_header},
    )

    assert response.status_code == 200
    assert response.text == _RANGE_CONTENT


@pytest.mark.asyncio
async def test_resource_range_served_from_cached_markdown_response(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    session_maker,
    fake_read_cache,
):
    """The cache stores full bytes; ranges slice per request after retrieval."""
    create_response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "RangedNote",
            "directory": "test",
            "content": "Original file content",
        },
    )
    assert create_response.status_code == 202
    created = create_response.json()

    accepted_content = "# RangedNote\n\nRange me.\n"
    repository = NoteContentRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        await repository.upsert(
            session,
            {
                "entity_id": created["id"],
                "markdown_content": accepted_content,
                "db_version": 42,
                "db_checksum": "ranged-checksum",
                "file_write_status": "pending",
                "last_source": "test",
            },
        )
    url = f"{v2_project_url}/resource/{created['external_id']}"

    # First read warms the markdown read cache with the full body.
    warm = await client.get(url)
    assert warm.status_code == 200
    assert warm.text == accepted_content
    assert len(fake_read_cache.payloads) == 1

    total = len(accepted_content.encode("utf-8"))
    ranged = await client.get(url, headers={"Range": "bytes=2-11"})
    assert ranged.status_code == 206
    assert ranged.content == accepted_content.encode("utf-8")[2:12]
    assert ranged.headers["content-range"] == f"bytes 2-11/{total}"

    # And a later full read still serves the complete cached body.
    full_after = await client.get(url)
    assert full_after.status_code == 200
    assert full_after.text == accepted_content
