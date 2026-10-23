"""Create and update routes refuse a note path that leaves the project.

sanitize_for_directory keeps dots, so before this rule a ".." directory reached
the stored file_path through POST /knowledge/write and POST /knowledge/entities.
Moves already refused such paths; creates and updates now use the same rule.
"""

from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from basic_memory.mcp.tools import write_note
from basic_memory.models import Entity as EntityModel, Project

TRAVERSAL_DIRECTORIES = ["../outside", "notes/../../outside", "..\\outside"]


async def _entity_count(session_maker) -> int:
    async with session_maker() as session:
        return (await session.execute(select(func.count()).select_from(EntityModel))).scalar_one()


def _files_beside_project(test_project: Project) -> set[Path]:
    """Every markdown file in the project's parent, so an escaping write would show up."""
    return set(Path(test_project.path).parent.rglob("*.md"))


@pytest.mark.asyncio
@pytest.mark.parametrize("directory", TRAVERSAL_DIRECTORIES)
async def test_write_route_refuses_traversal_directory(
    client: AsyncClient,
    v2_project_url: str,
    test_project: Project,
    session_maker,
    directory: str,
) -> None:
    entities_before = await _entity_count(session_maker)
    files_before = _files_beside_project(test_project)

    response = await client.post(
        f"{v2_project_url}/knowledge/write",
        json={"note": {"title": "Escape", "directory": directory, "content": "marker"}},
    )

    assert response.status_code == 400
    assert "Invalid destination path" in response.json()["detail"]
    assert await _entity_count(session_maker) == entities_before
    assert _files_beside_project(test_project) == files_before


@pytest.mark.asyncio
@pytest.mark.parametrize("directory", TRAVERSAL_DIRECTORIES)
async def test_entities_route_refuses_traversal_directory(
    client: AsyncClient,
    v2_project_url: str,
    test_project: Project,
    session_maker,
    directory: str,
) -> None:
    entities_before = await _entity_count(session_maker)
    files_before = _files_beside_project(test_project)

    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Escape", "directory": directory, "content": "marker"},
    )

    assert response.status_code == 400
    assert "Invalid destination path" in response.json()["detail"]
    assert await _entity_count(session_maker) == entities_before
    assert _files_beside_project(test_project) == files_before


@pytest.mark.asyncio
async def test_entity_put_refuses_moving_into_traversal_directory(
    client: AsyncClient,
    v2_project_url: str,
    test_project: Project,
) -> None:
    created = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Stay", "directory": "notes", "content": "original"},
    )
    assert created.status_code == 202
    entity_id = created.json()["external_id"]

    response = await client.put(
        f"{v2_project_url}/knowledge/entities/{entity_id}",
        json={"title": "Stay", "directory": "../outside", "content": "moved"},
    )

    assert response.status_code == 400
    assert "Invalid destination path" in response.json()["detail"]
    stored = (Path(test_project.path) / "notes/Stay.md").read_text(encoding="utf-8")
    assert "original" in stored
    assert not (Path(test_project.path).parent / "outside" / "Stay.md").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("directory", ["/abs", "C:/abs", "\\\\host\\abs"])
async def test_absolute_directory_stays_inside_the_project(
    client: AsyncClient,
    v2_project_url: str,
    test_project: Project,
    directory: str,
) -> None:
    """sanitize_for_directory already strips roots and drive colons, so an absolute
    directory becomes a relative one under the project rather than an escape."""
    response = await client.post(
        f"{v2_project_url}/knowledge/write",
        json={"note": {"title": "Rooted", "directory": directory, "content": "marker"}},
    )

    assert response.status_code == 200
    file_path = response.json()["entity"]["file_path"]
    assert not file_path.startswith(("/", "\\"))
    assert ".." not in Path(file_path).parts
    assert (Path(test_project.path) / file_path).is_file()


@pytest.mark.asyncio
async def test_nested_directories_still_create_through_both_routes(
    client: AsyncClient,
    v2_project_url: str,
    test_project: Project,
) -> None:
    written = await client.post(
        f"{v2_project_url}/knowledge/write",
        json={"note": {"title": "Deep", "directory": "a/b/c", "content": "marker"}},
    )
    created = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Dotted v1.2", "directory": "a/b/c", "content": "marker"},
    )

    assert written.status_code == 200
    assert written.json()["entity"]["file_path"] == "a/b/c/Deep.md"
    assert created.status_code == 202
    assert created.json()["file_path"] == "a/b/c/Dotted v1.2.md"
    assert (Path(test_project.path) / "a/b/c/Deep.md").is_file()


@pytest.mark.asyncio
async def test_mcp_write_note_still_creates_nested_note(app, test_project: Project) -> None:
    result = await write_note(
        project=test_project.name,
        title="Through MCP",
        directory="mcp/nested",
        content="# Through MCP\nmarker",
    )

    assert "# Created note" in result
    assert "file_path: mcp/nested/Through MCP.md" in result
