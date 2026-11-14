"""The project change journal route: GET /v2/projects/{project_id}/changes.

Each accepted note write takes the next position in the project's journal. These
tests drive real writes through the API and read them back through the route.
"""

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import AsyncClient

from basic_memory import db
from basic_memory.api.v2.routers.project_router import router as project_router
from basic_memory.models import Project
from basic_memory.read_cache.routes import _ReadThrough
from basic_memory.schemas.v2 import ProjectChangesResponse


async def _changes(
    client: AsyncClient, v2_project_url: str, **params: int
) -> ProjectChangesResponse:
    response = await client.get(f"{v2_project_url}/changes", params=params)
    assert response.status_code == 200, response.text
    return ProjectChangesResponse.model_validate(response.json())


async def _create_note(client: AsyncClient, v2_project_url: str, title: str) -> dict[str, Any]:
    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": title, "directory": "notes", "content": f"# {title}\n\nFirst draft."},
        params={"fast": False},
    )
    assert response.status_code == 202, response.text
    return response.json()


async def _partition_position(engine_factory, project: Project) -> int:
    _, session_maker = engine_factory
    async with db.scoped_session(session_maker) as session:
        persisted = await session.get(Project, project.id)
        assert persisted is not None
        return persisted.partition_position


@pytest.mark.asyncio
async def test_changes_lists_create_update_move_delete_in_order(
    client: AsyncClient,
    v2_project_url: str,
    engine_factory,
    test_project: Project,
) -> None:
    head_before = (await _changes(client, v2_project_url)).head

    created = await _create_note(client, v2_project_url, "Journal Note")
    note_id = created["external_id"]

    updated = await client.put(
        f"{v2_project_url}/knowledge/entities/{note_id}",
        json={
            "title": "Journal Note",
            "directory": "notes",
            "content": "# Journal Note\n\nSecond draft with new content.",
        },
    )
    assert updated.status_code == 202, updated.text

    moved = await client.put(
        f"{v2_project_url}/knowledge/entities/{note_id}/move",
        json={"destination_path": "archive/Journal Note.md"},
    )
    assert moved.status_code == 202, moved.text

    deleted = await client.delete(f"{v2_project_url}/knowledge/entities/{note_id}")
    assert deleted.status_code == 202, deleted.text

    page = await _changes(client, v2_project_url, after=head_before)

    assert page.head == await _partition_position(engine_factory, test_project)
    assert page.head == head_before + 4
    assert page.after == head_before
    assert page.next_after == page.head
    assert page.has_more is False
    assert [change.operation for change in page.changes] == [
        "created",
        "updated",
        "moved",
        "deleted",
    ]
    assert [change.position for change in page.changes] == [
        head_before + 1,
        head_before + 2,
        head_before + 3,
        head_before + 4,
    ]
    assert {change.note_external_id for change in page.changes} == {note_id}

    created_change, updated_change, moved_change, deleted_change = page.changes
    assert created_change.file_path == "notes/Journal Note.md"
    assert created_change.previous_file_path is None
    assert created_change.title == "Journal Note"
    assert created_change.accepted_at.tzinfo is not None
    assert updated_change.db_checksum != created_change.db_checksum
    assert moved_change.previous_file_path == "notes/Journal Note.md"
    assert moved_change.file_path == "archive/Journal Note.md"
    assert deleted_change.file_path == "archive/Journal Note.md"
    assert deleted_change.source == "delete_note"


@pytest.mark.asyncio
async def test_changes_hides_internal_columns(
    client: AsyncClient,
    v2_project_url: str,
) -> None:
    await _create_note(client, v2_project_url, "Private Columns")

    response = await client.get(f"{v2_project_url}/changes")
    assert response.status_code == 200, response.text
    [change] = response.json()["changes"]

    assert set(change) == {
        "position",
        "operation",
        "note_external_id",
        "permalink",
        "title",
        "file_path",
        "previous_file_path",
        "accepted_at",
        "source",
        "db_version",
        "db_checksum",
        "actor_kind",
        "actor_name",
    }


@pytest.mark.asyncio
async def test_changes_after_head_is_empty(
    client: AsyncClient,
    v2_project_url: str,
    engine_factory,
    test_project: Project,
) -> None:
    empty = await _changes(client, v2_project_url)
    assert empty.head == 0
    assert empty.changes == []
    assert empty.next_after == 0
    assert empty.has_more is False

    await _create_note(client, v2_project_url, "Head Note")
    head = await _partition_position(engine_factory, test_project)

    page = await _changes(client, v2_project_url, after=head)

    assert page.head == head
    assert page.changes == []
    assert page.has_more is False
    assert page.next_after == head


@pytest.mark.asyncio
async def test_changes_pages_with_limit_return_every_change_once(
    client: AsyncClient,
    v2_project_url: str,
) -> None:
    created_ids = [
        (await _create_note(client, v2_project_url, f"Paged Note {index}"))["external_id"]
        for index in range(5)
    ]

    seen: list[int] = []
    seen_notes: list[str] = []
    after = 0
    pages = 0
    while True:
        page = await _changes(client, v2_project_url, after=after, limit=2)
        pages += 1
        assert len(page.changes) <= 2
        seen.extend(change.position for change in page.changes)
        seen_notes.extend(change.note_external_id for change in page.changes)
        assert page.next_after <= page.head
        after = page.next_after
        if not page.has_more:
            break
        assert pages < 10, "paging did not converge"

    assert pages == 3
    assert seen == [1, 2, 3, 4, 5]
    assert seen_notes == created_ids


@pytest.mark.asyncio
async def test_changes_shows_a_change_made_after_the_previous_read(
    client: AsyncClient,
    v2_project_url: str,
) -> None:
    first = await _changes(client, v2_project_url)

    created = await _create_note(client, v2_project_url, "Later Note")

    second = await _changes(client, v2_project_url, after=first.next_after)
    assert second.head == first.head + 1
    assert [change.note_external_id for change in second.changes] == [created["external_id"]]


def test_changes_route_is_never_read_cached() -> None:
    # A cached page would hide changes accepted after it was stored.
    [route] = [
        route
        for route in project_router.routes
        if isinstance(route, APIRoute) and route.path == "/projects/{project_id}/changes"
    ]

    assert not any(isinstance(depends.dependency, _ReadThrough) for depends in route.dependencies)


@pytest.mark.asyncio
async def test_changes_cursor_ahead_of_head_returns_409(
    client: AsyncClient,
    v2_project_url: str,
) -> None:
    await _create_note(client, v2_project_url, "Restored Note")

    response = await client.get(f"{v2_project_url}/changes", params={"after": 5})

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": {"code": "cursor_ahead_of_head", "head": 1}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"after": -1},
        {"limit": 0},
        {"limit": 1001},
    ],
)
async def test_changes_rejects_out_of_range_query(
    client: AsyncClient,
    v2_project_url: str,
    params: dict[str, int],
) -> None:
    response = await client.get(f"{v2_project_url}/changes", params=params)

    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_changes_accepts_limit_bounds(
    client: AsyncClient,
    v2_project_url: str,
) -> None:
    for limit in (1, 1000):
        response = await client.get(f"{v2_project_url}/changes", params={"limit": limit})
        assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_changes_unknown_project_matches_directory_tree_error(
    client: AsyncClient,
) -> None:
    unknown = str(uuid.uuid4())

    changes = await client.get(f"/v2/projects/{unknown}/changes")
    tree = await client.get(f"/v2/projects/{unknown}/directory/tree")

    assert changes.status_code == tree.status_code == 404
    assert changes.json() == tree.json()


def test_changes_openapi_describes_cursor_rules(app: FastAPI) -> None:
    operation = app.openapi()["paths"]["/v2/projects/{project_id}/changes"]["get"]

    description = operation["description"]
    for phrase in ("head", "next_after", "has_more", "cursor_ahead_of_head", "Markdown"):
        assert phrase in description
    assert "409" in operation["responses"]
