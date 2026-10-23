"""bm_move: notes and directories, one destination, guards and refusals (#1831).

These run the real ASGI stack through the shared `client` fixture.
"""

from typing import Any

import httpx
import pytest
from fastmcp.exceptions import ToolError

from basic_memory.mcp.clients import KnowledgeClient
from basic_memory.mcp.note_targets import ProjectTarget
from basic_memory.mcp.project_context import ProjectPathRoute
from basic_memory.mcp.tools import bm_move, bm_read, bm_write
from basic_memory.mcp.tools import move as move_module
from basic_memory.schemas.project_info import ProjectItem
from basic_memory.schemas.response import DirectoryMoveResult


async def _note(project_name: str, path: str = "notes/plan.md") -> dict[str, Any]:
    result = await bm_write(
        identifier=path, content="Body", output_format="json", project=project_name
    )
    assert isinstance(result, dict)
    return result


async def move_json(**arguments: Any) -> dict[str, Any]:
    result = await bm_move(output_format="json", **arguments)
    assert isinstance(result, dict)
    return result


# --- Notes ---


@pytest.mark.asyncio
async def test_move_to_a_path(client, test_project):
    await _note(test_project.name)

    result = await move_json(
        identifier="notes/plan.md", destination="archive/old-plan.md", project=test_project.name
    )

    assert result["action"] == "moved"
    assert result["file_path"] == "archive/old-plan.md"
    assert result["previous_file_path"] == "notes/plan.md"
    assert result["checksum"]


@pytest.mark.asyncio
async def test_trailing_slash_keeps_the_file_name(client, test_project):
    await _note(test_project.name)

    text = await bm_move(
        identifier="notes/plan.md", destination="archive/", project=test_project.name
    )

    assert isinstance(text, str)
    assert text.startswith("Moved notes/plan.md to archive/plan.md")


@pytest.mark.asyncio
async def test_move_into_existing_folder_spelling(client, test_project):
    await _note(test_project.name, "Archive/seed.md")
    await _note(test_project.name)

    result = await move_json(
        identifier="notes/plan.md", destination="archive/", project=test_project.name
    )
    text = await bm_move(
        identifier="Archive/plan.md", destination="notes/plan.md", project=test_project.name
    )

    assert result["file_path"] == "Archive/plan.md"
    assert result["requested_file_path"] == "archive/plan.md"
    assert isinstance(text, str) and "requested" not in text


@pytest.mark.asyncio
async def test_text_result_reports_the_folder_spelling(client, test_project):
    await _note(test_project.name, "Archive/seed.md")
    await _note(test_project.name)

    text = await bm_move(
        identifier="notes/plan.md", destination="archive/", project=test_project.name
    )

    assert isinstance(text, str)
    assert text.startswith("Moved notes/plan.md to Archive/plan.md\nrequested: archive/plan.md")


@pytest.mark.asyncio
async def test_refused_directory_move(client, test_project, monkeypatch):
    async def refused(*_args: Any, **_kwargs: Any) -> None:
        request = httpx.Request("POST", "http://test")
        answer = httpx.Response(400, request=request, json={"detail": "Bad destination"})
        error = ToolError("refused")
        error.__cause__ = httpx.HTTPStatusError("refused", request=request, response=answer)
        raise error

    monkeypatch.setattr(KnowledgeClient, "move_directory", refused)
    with pytest.raises(ToolError, match="^INVALID_ARGUMENTS: Bad destination. Nothing was moved."):
        await bm_move(path="drafts", destination="archive", project=test_project.name)


@pytest.mark.asyncio
async def test_markdown_suffix_may_change(client, test_project):
    await _note(test_project.name)

    result = await move_json(
        identifier="notes/plan.md", destination="notes/plan.markdown", project=test_project.name
    )

    assert result["file_path"] == "notes/plan.markdown"


@pytest.mark.asyncio
async def test_project_qualified_destination(client, test_project):
    await _note(test_project.name)
    prefix = test_project.permalink

    result = await move_json(
        identifier=f"{prefix}/notes/plan.md", destination=f"{prefix}/archive/plan.md"
    )

    assert result["file_path"] == f"{prefix}/archive/plan.md"
    assert result["previous_file_path"] == f"{prefix}/notes/plan.md"


@pytest.mark.asyncio
async def test_destination_prefix_matches_any_spelling_of_the_project(client, test_project):
    """'TEST-PROJECT/archive/' names the same project as 'test-project/...'."""
    await _note(test_project.name)
    prefix = test_project.permalink

    result = await move_json(
        identifier=f"{prefix}/notes/plan.md", destination=f"{prefix.upper()}/archive/"
    )

    assert result["file_path"] == f"{prefix}/archive/plan.md"


@pytest.mark.asyncio
async def test_project_root_destination(client, test_project):
    """'<project>/' is that project's root, for notes and directories alike."""
    await _note(test_project.name)
    await _note(test_project.name, "archive/drafts/a.md")
    prefix = test_project.permalink

    note = await move_json(identifier=f"{prefix}/notes/plan.md", destination=f"{prefix}/")
    directory = await move_json(path=f"{prefix}/archive/drafts", destination=f"{prefix}/")

    assert note["file_path"] == f"{prefix}/plan.md"
    assert directory["moved_files"] == [f"{prefix}/drafts/a.md"]


@pytest.mark.asyncio
async def test_expected_checksum_guards_the_move(client, test_project):
    written = await _note(test_project.name)

    with pytest.raises(ToolError, match=f"^REVISION_CONFLICT: .*{written['checksum']}"):
        await bm_move(
            identifier="notes/plan.md",
            destination="archive/",
            expected_checksum="stale",
            project=test_project.name,
        )
    result = await move_json(
        identifier="notes/plan.md",
        destination="archive/",
        expected_checksum=written["checksum"],
        project=test_project.name,
    )
    assert result["file_path"] == "archive/plan.md"


@pytest.mark.asyncio
async def test_occupied_destination(client, test_project):
    await _note(test_project.name)
    await _note(test_project.name, "archive/plan.md")

    with pytest.raises(ToolError, match="^ALREADY_EXISTS: .*Nothing was moved"):
        await bm_move(identifier="notes/plan.md", destination="archive/", project=test_project.name)


@pytest.mark.asyncio
async def test_unanswered_move_is_an_unknown_outcome(client, test_project, monkeypatch):
    await _note(test_project.name)

    async def lost(*_args: Any, **_kwargs: Any) -> None:
        error = ToolError("timed out")
        error.__cause__ = httpx.ReadTimeout("timed out")
        raise error

    monkeypatch.setattr(KnowledgeClient, "move_entity", lost)
    with pytest.raises(ToolError, match="^OUTCOME_UNKNOWN: "):
        await bm_move(identifier="notes/plan.md", destination="archive/", project=test_project.name)


@pytest.mark.asyncio
async def test_unanswered_directory_move_points_at_bm_list(client, test_project, monkeypatch):
    """A directory cannot be read with bm_read; listing it shows what moved."""

    async def lost(*_args: Any, **_kwargs: Any) -> None:
        error = ToolError("timed out")
        error.__cause__ = httpx.ReadTimeout("timed out")
        raise error

    monkeypatch.setattr(KnowledgeClient, "move_directory", lost)
    with pytest.raises(ToolError, match=r"(?s)^OUTCOME_UNKNOWN: .*bm_list\(path='archive'\)"):
        await bm_move(path="drafts", destination="archive", project=test_project.name)


# --- Directories ---


@pytest.mark.asyncio
async def test_move_a_directory(client, test_project):
    await _note(test_project.name, "drafts/a.md")
    await _note(test_project.name, "drafts/b.md")

    result = await move_json(path="drafts", destination="archive/", project=test_project.name)
    text = await bm_move(path="archive/drafts", destination="old", project=test_project.name)

    assert result["destination"] == "archive/drafts"
    assert sorted(result["moved_files"]) == ["archive/drafts/a.md", "archive/drafts/b.md"]
    assert text == "Moved directory archive/drafts to old (2 files)"
    moved = await bm_read("old/a.md", project=test_project.name)
    assert isinstance(moved, str) and "Body" in moved


@pytest.mark.asyncio
async def test_directory_move_reports_the_existing_folder_spelling(client, test_project):
    """Moving into 'archive/' beside 'Archive/' reports the folder the files are in."""
    await _note(test_project.name, "Archive/seed.md")
    await _note(test_project.name, "drafts/a.md")

    result = await move_json(path="drafts", destination="archive/", project=test_project.name)

    assert result["destination"] == "Archive/drafts"
    assert result["requested_destination"] == "archive/drafts"
    assert result["moved_files"] == ["Archive/drafts/a.md"]


@pytest.mark.asyncio
async def test_guarded_move_of_a_note_already_gone(client, test_project):
    """A pinned revision of a note that no longer exists is a conflict, not a miss."""
    with pytest.raises(ToolError, match="^REVISION_CONFLICT: 'notes/gone.md' was deleted"):
        await bm_move(
            identifier="notes/gone.md",
            destination="archive/",
            expected_checksum="abc",
            project=test_project.name,
        )


@pytest.mark.asyncio
async def test_partial_directory_move_is_an_error(client, test_project, monkeypatch):
    async def partial(*_args: Any, **_kwargs: Any) -> DirectoryMoveResult:
        return DirectoryMoveResult.model_validate(
            {
                "total_files": 2,
                "successful_moves": 1,
                "failed_moves": 1,
                "moved_files": ["archive/a.md"],
                "errors": [{"path": "drafts/b.md", "error": "locked"}],
            }
        )

    monkeypatch.setattr(KnowledgeClient, "move_directory", partial)
    with pytest.raises(ToolError, match="^PARTIAL_FAILURE: Moved 1 of 2.*drafts/b.md: locked"):
        await bm_move(path="drafts", destination="archive", project=test_project.name)


@pytest.mark.asyncio
async def test_missing_directory(client, test_project):
    with pytest.raises(ToolError, match="^NOT_FOUND: No files under 'nope'"):
        await bm_move(path="nope", destination="archive", project=test_project.name)


# --- Refusals ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "code", "message"),
    [
        ({"destination": "a/"}, "INVALID_ARGUMENTS", "exactly one"),
        (
            {"identifier": "notes/plan.md", "path": "notes", "destination": "a/"},
            "INVALID_ARGUMENTS",
            "exactly one",
        ),
        (
            {"path": "notes", "destination": "a", "expected_checksum": "x"},
            "INVALID_ARGUMENTS",
            "directory move cannot take one",
        ),
        ({"identifier": "notes/plan.md", "destination": "../x.md"}, "OUTSIDE_PROJECT", "outside"),
        (
            {"identifier": "notes/plan.md", "destination": "notes/plan.md"},
            "INVALID_ARGUMENTS",
            "already at",
        ),
        (
            {"identifier": "notes/plan.md", "destination": "archive/2025"},
            "INVALID_ARGUMENTS",
            "no file extension",
        ),
        (
            {"identifier": "notes/plan.md", "destination": "notes/plan.txt"},
            "INVALID_ARGUMENTS",
            "keeps the file type",
        ),
        ({"path": "/", "destination": "x"}, "INVALID_ARGUMENTS", "project root cannot"),
        ({"path": "notes", "destination": "/"}, "INVALID_ARGUMENTS", "'notes' is already there"),
        ({"path": "notes", "destination": ""}, "INVALID_ARGUMENTS", "cannot move to the project"),
    ],
)
async def test_invalid_arguments(client, test_project, arguments, code, message):
    await _note(test_project.name)

    with pytest.raises(ToolError, match=f"^{code}: .*{message}"):
        await bm_move(project=test_project.name, **arguments)


@pytest.mark.asyncio
async def test_destination_naming_another_project(client, test_project, second_project):
    await _note(test_project.name)

    with pytest.raises(ToolError, match=f"^UNSUPPORTED: .*project '{second_project.name}'"):
        await bm_move(
            identifier="notes/plan.md",
            destination=f"{second_project.permalink}/plan.md",
            project=test_project.name,
        )


@pytest.mark.asyncio
async def test_guarded_move_of_a_deleted_note(client, test_project, monkeypatch):
    """A vanished revision says so; it never suggests expected_checksum='None'."""
    written = await _note(test_project.name)

    async def deleted(*_args: Any, **_kwargs: Any) -> None:
        request = httpx.Request("PUT", "http://test")
        answer = httpx.Response(
            409, request=request, json={"detail": {"message": "gone", "db_checksum": None}}
        )
        error = ToolError("conflict")
        error.__cause__ = httpx.HTTPStatusError("409", request=request, response=answer)
        raise error

    monkeypatch.setattr(KnowledgeClient, "move_entity", deleted)
    with pytest.raises(ToolError, match="^REVISION_CONFLICT: .*was deleted") as refused:
        await bm_move(
            identifier="notes/plan.md",
            destination="archive/",
            expected_checksum=written["checksum"],
            project=test_project.name,
        )
    assert "None" not in str(refused.value)


@pytest.mark.asyncio
async def test_missing_directory_hint_is_qualified(client, test_project):
    prefix = test_project.permalink

    with pytest.raises(ToolError, match=f"bm_list\\(path='{prefix}'\\)"):
        await bm_move(path=f"{prefix}/nope", destination=f"{prefix}/archive")


@pytest.mark.parametrize(
    ("destination", "relative"),
    [
        ("team/research/archive/a.md", "archive/a.md"),
        ("Team/Research/archive/", "archive/"),
        ("team/research/", "/"),
        ("team/archive/a.md", "team/archive/a.md"),
    ],
)
def test_workspace_qualified_destination_drops_the_whole_prefix(destination, relative):
    """A Cloud route names its project as 'team/research'; both segments are the prefix."""
    target = ProjectTarget(
        client=httpx.AsyncClient(),
        project=ProjectItem(id=1, external_id="p-1", name="research", path="/research"),
        route=ProjectPathRoute(project="team/research", path="notes/a.md", stripped=True),
    )

    assert move_module._project_relative_destination(target, destination, "text") == relative
