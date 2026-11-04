"""bm_move with destination_project: a write there, then a delete here (#1842).

These run the real ASGI stack through the shared `client` fixture.
"""

from pathlib import Path
from typing import Any, override

import httpx
import pytest
from fastapi import FastAPI
from fastmcp.exceptions import ToolError

from basic_memory.index.local_project import LocalProjectIndexRunner
from basic_memory.mcp.clients import KnowledgeClient
from basic_memory.mcp.cross_project_move import _files_under, move_note_across
from basic_memory.mcp.note_targets import ProjectTarget
from basic_memory.mcp.project_context import ProjectPathRoute
from basic_memory.mcp.tools import bm_move, bm_read, bm_write
from basic_memory.models import Project
from basic_memory.schemas.project_info import ProjectItem

NOTE = "---\ntitle: Plan\ntags: [launch]\n---\n\n# Plan\n\n- [goal] ship it\n"


async def _write(project: Project, path: str, content: str = NOTE) -> dict[str, Any]:
    result = await bm_write(
        identifier=path, content=content, output_format="json", project=project.name
    )
    assert isinstance(result, dict)
    return result


async def _read(project: Project, path: str) -> dict[str, Any]:
    result = await bm_read(identifier=path, output_format="json", project=project.name)
    assert isinstance(result, dict)
    return result


async def _note_id(client: httpx.AsyncClient, project: Project, path: str) -> str:
    resolved = await KnowledgeClient(client, project.external_id).resolve_entity_response(
        path, strict=True
    )
    return resolved.external_id


async def _missing(project: Project, path: str) -> bool:
    try:
        await bm_read(identifier=path, project=project.name)
    except ToolError as error:
        return str(error).startswith("NOT_FOUND")
    return False


# --- Notes ---


@pytest.mark.asyncio
async def test_a_note_moves_into_another_project(client, test_project, second_project):
    await _write(test_project, "notes/plan.md")
    source_id = await _note_id(client, test_project, "notes/plan.md")

    result = await bm_move(
        identifier="notes/plan.md",
        destination="inbox/plan.md",
        destination_project=second_project.name,
        project=test_project.name,
        output_format="json",
    )

    assert result == {
        "action": "moved",
        "previous_project": test_project.name,
        "previous_file_path": "notes/plan.md",
        "project": second_project.name,
        "file_path": "inbox/plan.md",
    }
    moved = await _read(second_project, "inbox/plan.md")
    assert "- [goal] ship it" in moved["content"]
    assert "tags:" in moved["content"]
    # A new note there: its own id; the source project no longer has it.
    assert await _note_id(client, second_project, "inbox/plan.md") != source_id
    assert await _missing(test_project, "notes/plan.md")


@pytest.mark.asyncio
async def test_a_renaming_move_keeps_the_title(client, test_project, second_project):
    await _write(test_project, "notes/plan.md", NOTE.replace("title: Plan", "title: Launch"))

    await bm_move(
        identifier="notes/plan.md",
        destination="inbox/roadmap.md",
        destination_project=second_project.name,
        project=test_project.name,
    )

    moved = await _read(second_project, "inbox/roadmap.md")
    assert "title: Launch" in moved["content"]


@pytest.mark.asyncio
async def test_a_trailing_slash_keeps_the_file_name(client, test_project, second_project):
    await _write(test_project, "notes/plan.md")

    text = await bm_move(
        identifier="notes/plan.md",
        destination="inbox/",
        destination_project=second_project.name,
        project=test_project.name,
    )

    assert text == (
        f"Moved notes/plan.md (project {test_project.name}) to inbox/plan.md "
        f"(project {second_project.name})"
    )
    assert (Path(second_project.path) / "inbox/plan.md").exists()


@pytest.mark.asyncio
async def test_an_occupied_destination_leaves_the_source(client, test_project, second_project):
    await _write(test_project, "notes/plan.md")
    await _write(second_project, "inbox/plan.md", "# Other\n")

    with pytest.raises(ToolError, match="^ALREADY_EXISTS: .*Nothing was moved"):
        await bm_move(
            identifier="notes/plan.md",
            destination="inbox/plan.md",
            destination_project=second_project.name,
            project=test_project.name,
        )

    assert not await _missing(test_project, "notes/plan.md")
    assert "# Other" in (await _read(second_project, "inbox/plan.md"))["content"]


@pytest.mark.asyncio
async def test_a_stale_checksum_moves_nothing(client, test_project, second_project):
    written = await _write(test_project, "notes/plan.md")
    await bm_write(
        identifier="notes/plan.md", content="# Changed\n", overwrite=True, project=test_project.name
    )

    with pytest.raises(ToolError, match="^REVISION_CONFLICT: "):
        await bm_move(
            identifier="notes/plan.md",
            destination="inbox/plan.md",
            destination_project=second_project.name,
            expected_checksum=written["checksum"],
            project=test_project.name,
        )

    assert await _missing(second_project, "inbox/plan.md")


@pytest.mark.asyncio
async def test_a_locked_note_is_refused_before_anything_is_written(
    client, test_project, second_project
):
    await _write(test_project, "notes/locked.md", "---\nlocked: true\n---\n\nKeep.\n")

    with pytest.raises(ToolError, match="^LOCKED: "):
        await bm_move(
            identifier="notes/locked.md",
            destination="inbox/",
            destination_project=second_project.name,
            project=test_project.name,
        )

    assert await _missing(second_project, "inbox/locked.md")


@pytest.mark.asyncio
async def test_the_same_project_is_an_ordinary_move(client, test_project):
    await _write(test_project, "notes/plan.md")
    note_id = await _note_id(client, test_project, "notes/plan.md")

    result = await bm_move(
        identifier="notes/plan.md",
        destination="archive/",
        destination_project=test_project.name,
        project=test_project.name,
        output_format="json",
    )

    assert isinstance(result, dict)
    assert result["file_path"] == "archive/plan.md"
    # An ordinary move keeps the note's identity.
    assert await _note_id(client, test_project, "archive/plan.md") == note_id


@pytest.mark.asyncio
async def test_a_destination_naming_another_project_points_at_destination_project(
    client, test_project, second_project
):
    await _write(test_project, "notes/plan.md")

    with pytest.raises(ToolError, match=f"destination_project='{second_project.name}'"):
        await bm_move(
            identifier="notes/plan.md",
            destination=f"{second_project.permalink}/plan.md",
            project=test_project.name,
        )


class FailingRequests(httpx.AsyncBaseTransport):
    """The real API, except for requests with one HTTP method.

    "refuse" answers 403, as for a member who may not change this project;
    "lose" never answers, as when the connection drops after the request left.
    """

    def __init__(self, app: FastAPI, mode: str, method: str = "DELETE") -> None:
        self.inner = httpx.ASGITransport(app=app)
        self.mode = mode
        self.method = method

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != self.method:
            return await self.inner.handle_async_request(request)
        if self.mode == "lose":
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(403, json={"detail": "You cannot change this project."})


def _target(client: httpx.AsyncClient, project: Project, path: str = "") -> ProjectTarget:
    return ProjectTarget(
        client=client,
        project=ProjectItem(
            id=project.id, external_id=project.external_id, name=project.name, path=project.path
        ),
        route=ProjectPathRoute(project=project.name, path=path, stripped=False),
        project_ref=project.name,
    )


async def _move_with(
    app: FastAPI, mode: str, source: Project, target: httpx.AsyncClient | None, dest: Project
) -> None:
    """Move notes/plan.md with the source's DELETEs failing; the target too when None."""
    async with httpx.AsyncClient(
        transport=FailingRequests(app, mode), base_url="http://test"
    ) as failing:
        await move_note_across(
            _target(failing, source, "notes/plan.md"),
            _target(target or failing, dest),
            "notes/plan.md",
            "inbox/plan.md",
            None,
            "text",
        )


@pytest.mark.asyncio
async def test_a_refused_source_delete_removes_the_copy(app, client, test_project, second_project):
    await _write(test_project, "notes/plan.md")

    with pytest.raises(ToolError, match="^FORBIDDEN: You cannot change .*Nothing was moved"):
        await _move_with(app, "refuse", test_project, client, second_project)

    assert not await _missing(test_project, "notes/plan.md")
    assert await _missing(second_project, "inbox/plan.md")


@pytest.mark.asyncio
async def test_an_unanswered_source_delete_keeps_the_copy(
    app, client, test_project, second_project
):
    """The source may be gone; removing the copy then could lose the note."""
    await _write(test_project, "notes/plan.md")

    with pytest.raises(ToolError, match=r"^OUTCOME_UNKNOWN: 'notes/plan.md' was copied to"):
        await _move_with(app, "lose", test_project, client, second_project)

    assert not await _missing(second_project, "inbox/plan.md")


@pytest.mark.asyncio
async def test_a_refused_target_write_leaves_the_source(app, client, test_project, second_project):
    await _write(test_project, "notes/plan.md")

    async with httpx.AsyncClient(
        transport=FailingRequests(app, "refuse", method="POST"), base_url="http://test"
    ) as refusing:
        with pytest.raises(ToolError, match="^FORBIDDEN: .*Nothing was moved"):
            await move_note_across(
                _target(client, test_project, "notes/plan.md"),
                _target(refusing, second_project),
                "notes/plan.md",
                "inbox/plan.md",
                None,
                "text",
            )

    assert not await _missing(test_project, "notes/plan.md")


@pytest.mark.asyncio
async def test_a_non_markdown_destination_is_refused(client, test_project, second_project):
    await _write(test_project, "notes/plan.md")

    with pytest.raises(ToolError, match="^INVALID_ARGUMENTS: 'inbox/plan.txt' is not"):
        await bm_move(
            identifier="notes/plan.md",
            destination="inbox/plan.txt",
            destination_project=second_project.name,
            project=test_project.name,
        )


@pytest.mark.asyncio
async def test_a_copy_that_cannot_be_removed_is_reported(app, test_project, second_project):
    await _write(test_project, "notes/plan.md")

    with pytest.raises(ToolError, match="^PARTIAL_FAILURE: .*now in both projects"):
        await _move_with(app, "refuse", test_project, None, second_project)

    assert not await _missing(test_project, "notes/plan.md")
    assert not await _missing(second_project, "inbox/plan.md")


# --- Directories ---


@pytest.mark.asyncio
async def test_a_directory_moves_note_by_note(client, test_project, second_project):
    await _write(test_project, "drafts/a.md", "# A\n")
    await _write(test_project, "drafts/deep/b.md", "# B\n")

    result = await bm_move(
        path="drafts",
        destination="imported/",
        destination_project=second_project.name,
        project=test_project.name,
        output_format="json",
    )

    assert isinstance(result, dict)
    assert result["destination"] == "imported/drafts"
    assert sorted(result["moved_files"]) == ["imported/drafts/a.md", "imported/drafts/deep/b.md"]
    assert result["failed"] == []
    assert "# B" in (await _read(second_project, "imported/drafts/deep/b.md"))["content"]
    assert await _missing(test_project, "drafts/a.md")
    assert await _missing(test_project, "drafts/deep/b.md")


@pytest.mark.asyncio
async def test_a_directory_move_reports_what_stayed(client, test_project, second_project):
    await _write(test_project, "drafts/a.md", "# A\n")
    await _write(test_project, "drafts/b.md", "# B\n")
    await _write(second_project, "drafts/b.md", "# Taken\n")

    with pytest.raises(ToolError, match=r"^PARTIAL_FAILURE: Moved 1 of 2 files .*drafts/b.md"):
        await bm_move(
            path="drafts",
            destination="drafts",
            destination_project=second_project.name,
            project=test_project.name,
        )

    assert await _missing(test_project, "drafts/a.md")
    assert not await _missing(test_project, "drafts/b.md")


@pytest.mark.asyncio
async def test_an_empty_directory_is_not_found(client, test_project, second_project):
    with pytest.raises(ToolError, match="^NOT_FOUND: No files under 'nothing'"):
        await bm_move(
            path="nothing",
            destination="x",
            destination_project=second_project.name,
            project=test_project.name,
        )


@pytest.mark.asyncio
async def test_a_directory_move_as_text(client, test_project, second_project):
    await _write(test_project, "drafts/a.md", "# A\n")

    text = await bm_move(
        path="drafts",
        destination="drafts",
        destination_project=second_project.name,
        project=test_project.name,
    )

    assert text == (
        f"Moved directory drafts (project {test_project.name}) to drafts "
        f"(project {second_project.name}) (1 files)"
    )


@pytest.mark.asyncio
async def test_a_file_that_is_not_a_note_stays(
    client, test_project, second_project, project_repository, session_maker
):
    await _write(test_project, "drafts/a.md", "# A\n")
    (Path(test_project.path) / "drafts/data.txt").write_text("raw\n", encoding="utf-8")
    await LocalProjectIndexRunner(
        project_repository=project_repository, session_maker=session_maker
    ).index_project(test_project.id)

    with pytest.raises(ToolError, match=r"^PARTIAL_FAILURE: Moved 1 of 2 .*only notes move"):
        await bm_move(
            path="drafts",
            destination="drafts",
            destination_project=second_project.name,
            project=test_project.name,
        )

    assert (Path(test_project.path) / "drafts/data.txt").exists()


@pytest.mark.asyncio
async def test_the_project_root_cannot_move(client, test_project, second_project):
    with pytest.raises(ToolError, match="^INVALID_ARGUMENTS: The project root cannot be moved"):
        await bm_move(
            path="/",
            destination="old",
            destination_project=second_project.name,
            project=test_project.name,
        )


@pytest.mark.asyncio
async def test_the_same_project_directory_move_is_an_ordinary_move(client, test_project):
    await _write(test_project, "drafts/a.md", "# A\n")

    result = await bm_move(
        path="drafts",
        destination="archive",
        destination_project=test_project.name,
        project=test_project.name,
        output_format="json",
    )

    assert isinstance(result, dict)
    assert result["moved_files"] == ["archive/a.md"]


@pytest.mark.asyncio
async def test_the_listing_reads_every_page(client, test_project):
    for name in ("a", "b", "c"):
        await _write(test_project, f"drafts/{name}.md", f"# {name}\n")

    files = await _files_under(_target(client, test_project), "drafts", page_size=1)

    assert sorted(node.file_path for node in files if node.file_path) == [
        "drafts/a.md",
        "drafts/b.md",
        "drafts/c.md",
    ]
