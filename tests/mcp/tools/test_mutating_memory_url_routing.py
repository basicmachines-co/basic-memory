"""Fail-closed memory URL routing for mutating MCP tools."""

import importlib

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from basic_memory.mcp.tools.move_note import move_note
from basic_memory.mcp.tools.read_note import read_note
from basic_memory.mcp.tools.write_note import write_note


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module_name", "tool_name", "tool_args"),
    [
        (
            "basic_memory.mcp.tools.delete_note",
            "delete_note",
            {"identifier": "memory://other-project/note"},
        ),
        (
            "basic_memory.mcp.tools.move_note",
            "move_note",
            {
                "identifier": "memory://other-project/note",
                "destination_path": "archive/note.md",
            },
        ),
        (
            "basic_memory.mcp.tools.move_note",
            "move_note",
            {
                "identifier": "memory://other-project/directory",
                "destination_path": "archive/directory",
                "is_directory": True,
            },
        ),
    ],
)
async def test_mutating_tools_require_strict_project_routing(
    client,
    test_project,
    monkeypatch,
    module_name: str,
    tool_name: str,
    tool_args: dict[str, str | bool],
) -> None:
    """Scope-hidden project prefixes must stop before any mutation client call."""
    tool_module = importlib.import_module(module_name)

    async def reject_scope_hidden_route(*args: object, **kwargs: object) -> None:
        assert kwargs["strict_project_routing"] is True
        assert kwargs["allow_missing_project_fallback"] is True
        raise ToolError("This API key does not have access to this project")

    monkeypatch.setattr(
        tool_module,
        "resolve_project_and_path",
        reject_scope_hidden_route,
    )

    tool = getattr(tool_module, tool_name)
    with pytest.raises(ToolError, match="does not have access"):
        await tool(project=test_project.name, **tool_args)


@pytest.mark.asyncio
async def test_directory_move_normalizes_project_memory_url(client, test_project) -> None:
    """Directory moves pass a project-relative source path to the mutation API."""
    await write_note(
        project=test_project.name,
        title="Strict Directory Move",
        directory="strict-directory-source",
        content="# Strict Directory Move\nMove this note with a memory URL.",
    )

    result = await move_note(
        project=test_project.name,
        identifier=f"memory://{test_project.name}/strict-directory-source",
        destination_path="strict-directory-destination",
        is_directory=True,
        output_format="json",
    )

    assert isinstance(result, dict)
    assert result["moved"] is True
    assert result["total_files"] == 1
    assert result["successful_moves"] == 1
    assert result["failed_moves"] == 0

    moved_note = await read_note(
        "strict-directory-destination/strict-directory-move",
        project=test_project.name,
    )
    assert "Move this note with a memory URL." in moved_note
