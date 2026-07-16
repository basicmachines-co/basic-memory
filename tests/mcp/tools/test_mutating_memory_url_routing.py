"""Fail-closed memory URL routing for mutating MCP tools."""

import importlib

import pytest
from mcp.server.fastmcp.exceptions import ToolError


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
    ],
)
async def test_mutating_tools_require_strict_project_routing(
    client,
    test_project,
    monkeypatch,
    module_name: str,
    tool_name: str,
    tool_args: dict[str, str],
) -> None:
    """Scope-hidden project prefixes must stop before any mutation client call."""
    tool_module = importlib.import_module(module_name)

    async def reject_scope_hidden_route(*args: object, **kwargs: object) -> None:
        assert kwargs["strict_project_routing"] is True
        raise ToolError("This API key does not have access to this project")

    monkeypatch.setattr(
        tool_module,
        "resolve_project_and_path",
        reject_scope_hidden_route,
    )

    tool = getattr(tool_module, tool_name)
    with pytest.raises(ToolError, match="does not have access"):
        await tool(project=test_project.name, **tool_args)
