"""Regression tests for move_note edge cases found by the integration bug hunt.

Bug #11: move_note did not resolve memory:// URL identifiers, even though its
docstring advertises them and read_note/edit_note/delete_note all accept them.

Bug #12: move_note's structural "<seg>/projects/<seg>/file.md" heuristic wrongly
rejected legitimate same-project nested moves (e.g. "notes/projects/2025/file.md")
as cross-project moves. The heuristic was removed; cross-project detection now relies
on the leading segment matching a known project name plus the post-move outcome
backstop.

These are integration tests: real MCP server -> FastAPI -> database -> filesystem.
"""

import pytest
from fastmcp import Client


def _result(r):
    return r.structured_content["result"]


# --- Bug #11: memory:// URL resolution ---


@pytest.mark.asyncio
async def test_move_note_accepts_memory_url(mcp_server, app, test_project):
    """move_note should accept a memory:// URL identifier like read/edit/delete do."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Memory Url Move",
                "directory": "src",
                "content": "# Memory Url Move\n\nbody",
                "output_format": "json",
            },
        )
        move = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "memory://src/memory-url-move",
                "destination_path": "dst/memory-url-move.md",
                "output_format": "json",
            },
        )
        result = _result(move)
        assert result["moved"] is True, (
            f"move_note should resolve memory:// URLs but failed: {result.get('error')}"
        )
        assert result["file_path"] == "dst/memory-url-move.md"


@pytest.mark.asyncio
async def test_move_note_bare_permalink_works_control(mcp_server, app, test_project):
    """Control: bare permalink (no memory://) DOES work for move_note."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Bare Permalink Move",
                "directory": "src",
                "content": "# Bare Permalink Move\n\nbody",
                "output_format": "json",
            },
        )
        move = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "src/bare-permalink-move",
                "destination_path": "dst/bare-permalink-move.md",
                "output_format": "json",
            },
        )
        result = _result(move)
        assert result["moved"] is True, result.get("error")


@pytest.mark.asyncio
async def test_delete_note_accepts_memory_url_control(mcp_server, app, test_project):
    """Control: delete_note DOES resolve memory:// URLs (proves the contract)."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Delete Memory Url",
                "directory": "src",
                "content": "# Delete Memory Url\n\nbody",
                "output_format": "json",
            },
        )
        d = await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": "memory://src/delete-memory-url",
                "output_format": "json",
            },
        )
        assert _result(d)["deleted"] is True


@pytest.mark.asyncio
async def test_edit_note_accepts_memory_url_control(mcp_server, app, test_project):
    """Control: edit_note DOES resolve memory:// URLs (proves the contract)."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Edit Memory Url",
                "directory": "src",
                "content": "# Edit Memory Url\n\nbody",
                "output_format": "json",
            },
        )
        e = await client.call_tool(
            "edit_note",
            {
                "project": test_project.name,
                "identifier": "memory://src/edit-memory-url",
                "operation": "append",
                "content": "\nEDITED",
                "output_format": "json",
            },
        )
        ec = _result(e)
        assert ec.get("error") is None
        assert ec.get("fileCreated") is False


# --- Bug #12: nested 'projects' folder false positive ---


@pytest.mark.asyncio
async def test_move_into_nested_projects_folder_not_flagged(mcp_server, app, test_project):
    """A legit nested folder like notes/projects/2025/note.md must NOT be flagged
    as a cross-project move (the structural 'projects'-segment heuristic was removed)."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Nested Projects Note",
                "directory": "inbox",
                "content": "# Nested\n\nbody",
                "output_format": "json",
            },
        )
        move = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "inbox/nested-projects-note",
                "destination_path": "notes/projects/2025/nested-projects-note.md",
                "output_format": "json",
            },
        )
        result = _result(move)
        assert result.get("error") != "CROSS_PROJECT_MOVE_NOT_SUPPORTED", (
            "Legit nested 'projects' folder wrongly flagged as cross-project move"
        )
        assert result["moved"] is True, result.get("error")
