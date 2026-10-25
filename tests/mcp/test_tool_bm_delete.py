"""bm_delete: notes and directories, missing notes are errors, guards (#1831).

These run the real ASGI stack through the shared `client` fixture.
"""

from typing import Any

import httpx
import pytest
from fastmcp.exceptions import ToolError

from basic_memory.mcp.clients import KnowledgeClient
from basic_memory.mcp.tools import bm_delete, bm_read, bm_write
from basic_memory.schemas.response import DeleteEntitiesResponse, DirectoryDeleteResult


async def _note(project_name: str, path: str = "notes/plan.md") -> dict[str, Any]:
    result = await bm_write(
        identifier=path, content="Body", output_format="json", project=project_name
    )
    assert isinstance(result, dict)
    return result


# --- Notes ---


@pytest.mark.asyncio
async def test_delete_a_note(client, test_project):
    await _note(test_project.name)

    text = await bm_delete(identifier="notes/plan.md", project=test_project.name)

    assert text == "Deleted notes/plan.md"
    with pytest.raises(ToolError, match="^NOT_FOUND"):
        await bm_read("notes/plan.md", project=test_project.name)


@pytest.mark.asyncio
async def test_missing_note_is_an_error(client, test_project):
    with pytest.raises(ToolError, match="^NOT_FOUND: No note matches 'notes/nope.md'"):
        await bm_delete(identifier="notes/nope.md", project=test_project.name)


@pytest.mark.asyncio
async def test_expected_checksum_guards_the_delete(client, test_project):
    written = await _note(test_project.name)

    with pytest.raises(ToolError, match=f"^REVISION_CONFLICT: .*{written['checksum']}"):
        await bm_delete(
            identifier="notes/plan.md", expected_checksum="stale", project=test_project.name
        )
    result = await bm_delete(
        identifier="notes/plan.md",
        expected_checksum=written["checksum"],
        output_format="json",
        project=test_project.name,
    )
    assert result == {"action": "deleted", "title": "plan", "file_path": "notes/plan.md"}


@pytest.mark.asyncio
async def test_project_qualified_identifier(client, test_project):
    await _note(test_project.name)
    prefix = test_project.permalink

    text = await bm_delete(identifier=f"{prefix}/notes/plan.md")

    assert text == f"Deleted {prefix}/notes/plan.md"


@pytest.mark.asyncio
async def test_note_gone_by_the_time_the_delete_arrives(client, test_project, monkeypatch):
    await _note(test_project.name)

    async def already_gone(*_args: Any, **_kwargs: Any) -> DeleteEntitiesResponse:
        return DeleteEntitiesResponse(deleted=False)

    monkeypatch.setattr(KnowledgeClient, "delete_entity", already_gone)
    with pytest.raises(ToolError, match="^NOT_FOUND: .*already deleted"):
        await bm_delete(identifier="notes/plan.md", project=test_project.name)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "detail", "pattern"),
    [
        (409, {"message": "gone", "db_checksum": None}, "^REVISION_CONFLICT: .*was deleted"),
        (423, "Note is locked", "^LOCKED: Note is locked. Nothing was deleted."),
    ],
)
async def test_api_refusals(client, test_project, monkeypatch, status_code, detail, pattern):
    await _note(test_project.name)

    async def refused(*_args: Any, **_kwargs: Any) -> None:
        request = httpx.Request("DELETE", "http://test")
        answer = httpx.Response(status_code, request=request, json={"detail": detail})
        error = ToolError("refused")
        error.__cause__ = httpx.HTTPStatusError("refused", request=request, response=answer)
        raise error

    monkeypatch.setattr(KnowledgeClient, "delete_entity", refused)
    with pytest.raises(ToolError, match=pattern):
        await bm_delete(
            identifier="notes/plan.md", expected_checksum="abc", project=test_project.name
        )


@pytest.mark.asyncio
async def test_unanswered_delete_is_an_unknown_outcome(client, test_project, monkeypatch):
    await _note(test_project.name)

    async def lost(*_args: Any, **_kwargs: Any) -> None:
        error = ToolError("timed out")
        error.__cause__ = httpx.ReadTimeout("timed out")
        raise error

    monkeypatch.setattr(KnowledgeClient, "delete_entity", lost)
    with pytest.raises(ToolError, match="^OUTCOME_UNKNOWN: "):
        await bm_delete(identifier="notes/plan.md", project=test_project.name)


# --- Directories ---


@pytest.mark.asyncio
async def test_directory_delete_needs_recursive(client, test_project):
    await _note(test_project.name, "drafts/a.md")
    await _note(test_project.name, "drafts/b.md")

    with pytest.raises(ToolError, match="^INVALID_ARGUMENTS: 'drafts' is a directory with 2"):
        await bm_delete(path="drafts", project=test_project.name)
    result = await bm_delete(
        path="drafts", recursive=True, output_format="json", project=test_project.name
    )

    assert isinstance(result, dict)
    assert result["total_files"] == 2
    assert sorted(result["deleted_files"]) == ["drafts/a.md", "drafts/b.md"]


@pytest.mark.asyncio
async def test_missing_directory(client, test_project):
    with pytest.raises(ToolError, match="^NOT_FOUND: No directory 'nope'"):
        await bm_delete(path="nope", project=test_project.name)
    with pytest.raises(ToolError, match="^NOT_FOUND: No files under 'nope'"):
        await bm_delete(path="nope", recursive=True, project=test_project.name)


@pytest.mark.asyncio
async def test_directory_text_result(client, test_project):
    await _note(test_project.name, "drafts/a.md")

    text = await bm_delete(path="drafts", recursive=True, project=test_project.name)

    assert text == "Deleted directory drafts (1 files)"


@pytest.mark.asyncio
async def test_refused_directory_delete(client, test_project, monkeypatch):
    async def refused(*_args: Any, **_kwargs: Any) -> None:
        request = httpx.Request("POST", "http://test")
        answer = httpx.Response(423, request=request, json={"detail": "A note is locked."})
        error = ToolError("refused")
        error.__cause__ = httpx.HTTPStatusError("refused", request=request, response=answer)
        raise error

    monkeypatch.setattr(KnowledgeClient, "delete_directory", refused)
    with pytest.raises(ToolError, match="^LOCKED: A note is locked. Nothing was deleted."):
        await bm_delete(path="drafts", recursive=True, project=test_project.name)
    # A directory is looked at with bm_list, not bm_read.
    with pytest.raises(ToolError, match=r"bm_list\(path='drafts'\)"):
        await bm_delete(path="drafts", recursive=True, project=test_project.name)


@pytest.mark.asyncio
async def test_partial_directory_delete_is_an_error(client, test_project, monkeypatch):
    async def partial(*_args: Any, **_kwargs: Any) -> DirectoryDeleteResult:
        return DirectoryDeleteResult.model_validate(
            {
                "total_files": 2,
                "successful_deletes": 1,
                "failed_deletes": 1,
                "deleted_files": ["drafts/a.md"],
                "errors": [{"path": "drafts/b.md", "error": "locked"}],
            }
        )

    monkeypatch.setattr(KnowledgeClient, "delete_directory", partial)
    with pytest.raises(ToolError, match="^PARTIAL_FAILURE: Deleted 1 of 2.*drafts/b.md: locked"):
        await bm_delete(path="drafts", recursive=True, project=test_project.name)


# --- Refusals ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "exactly one"),
        ({"identifier": "a.md", "path": "notes"}, "exactly one"),
        ({"path": "notes", "expected_checksum": "x"}, "directory delete cannot take one"),
        ({"identifier": "a.md", "recursive": True}, "recursive applies to directories"),
        ({"path": "/", "recursive": True}, "project root cannot be deleted"),
    ],
)
async def test_invalid_arguments(client, test_project, arguments, message):
    with pytest.raises(ToolError, match=f"^INVALID_ARGUMENTS: .*{message}"):
        await bm_delete(project=test_project.name, **arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [" ", " / ", "\t", "\t/\t"])
async def test_a_blank_path_never_deletes_the_project(client, test_project, path):
    """The API reads a blank directory as the project root; bm_delete refuses it."""
    await bm_write(identifier="notes/keep.md", content="# Keep", project=test_project.name)

    with pytest.raises(ToolError, match="^INVALID_ARGUMENTS: The project root cannot"):
        await bm_delete(path=path, recursive=True, project=test_project.name)

    assert await bm_read(identifier="notes/keep.md", project=test_project.name)


@pytest.mark.asyncio
async def test_guarded_delete_of_a_note_already_gone(client, test_project):
    """A pinned revision of a note that no longer exists is a conflict, not a miss."""
    with pytest.raises(ToolError, match="^REVISION_CONFLICT: 'notes/gone.md' was deleted"):
        await bm_delete(
            identifier="notes/gone.md", expected_checksum="abc", project=test_project.name
        )
