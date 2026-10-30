"""A missing or blank project is refused with a recovery hint, not a pydantic error (#1984)."""

import json
from typing import Any

import pytest
from fastmcp import Client, FastMCP

from basic_memory.mcp import tools
from basic_memory.mcp.missing_project import MissingProjectMiddleware
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_params import OutputFormat, ProjectParam

SCOPED_TOOLS = {
    "bm_read": {"identifier": "notes/plan.md"},
    "bm_write": {"identifier": "notes/plan.md", "content": "# Plan"},
    "bm_edit": {"identifier": "notes/plan.md", "command": "append", "content": "tail"},
    "bm_move": {"identifier": "notes/plan.md", "destination": "archive/plan.md"},
    "bm_delete": {"identifier": "notes/plan.md"},
    "bm_list": {"path": "/"},
    "bm_find": {"path": "/"},
    "bm_context": {"identifier": "notes/plan.md"},
    "bm_schema": {"command": "validate"},
}

HINT = "Try instead: bm_project(command='list') to discover projects"


def _server(*registered: Any) -> FastMCP:
    server = FastMCP("missing-project")
    server.add_middleware(MissingProjectMiddleware())
    for tool in registered:
        server.tool(tool)
    return server


async def _call(server: FastMCP, name: str, arguments: dict[str, Any]):
    async with Client(server) as client:
        return await client.call_tool(name, arguments, raise_on_error=False)


def _text(result) -> str:
    return "".join(block.text for block in result.content)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", SCOPED_TOOLS)
@pytest.mark.parametrize("scope", [{}, {"project": None}, {"project": ""}, {"project": "   "}])
async def test_one_project_tool_refuses_missing_project(name: str, scope: dict[str, Any]):
    arguments = {**SCOPED_TOOLS[name], **scope, "output_format": "text"}
    result = await _call(_server(getattr(tools, name)), name, arguments)

    assert result.is_error
    text = _text(result)
    assert text.startswith("INVALID_ARGUMENTS:")
    assert f"{name} needs `project`" in text
    assert HINT in text


@pytest.mark.asyncio
@pytest.mark.parametrize("name", SCOPED_TOOLS)
async def test_json_output_format_gets_a_json_refusal(name: str):
    arguments = {**SCOPED_TOOLS[name], "output_format": "json"}
    result = await _call(_server(getattr(tools, name)), name, arguments)

    assert result.is_error
    payload = json.loads(_text(result))
    assert payload["error"] == "INVALID_ARGUMENTS"
    assert payload["try_instead"] == "bm_project(command='list') to discover projects"


@pytest.mark.asyncio
async def test_refusal_follows_the_tools_default_output_format():
    # bm_context defaults to JSON; bm_list defaults to text.
    context = await _call(_server(tools.bm_context), "bm_context", SCOPED_TOOLS["bm_context"])
    listing = await _call(_server(tools.bm_list), "bm_list", SCOPED_TOOLS["bm_list"])

    assert json.loads(_text(context))["error"] == "INVALID_ARGUMENTS"
    assert _text(listing).startswith("INVALID_ARGUMENTS:")


async def scoped_echo(note: str, *, project: ProjectParam, output_format: OutputFormat = "text"):
    """A tool that is not a core tool but requires project the same way."""
    return f"{project}:{note}"


async def unscoped_echo(note: str, project: str | None = None) -> str:
    """A tool whose project is optional."""
    return f"{project}:{note}"


@pytest.mark.asyncio
async def test_any_tool_requiring_project_is_refused_not_just_core_tools():
    result = await _call(_server(scoped_echo), "scoped_echo", {"note": "plan"})

    assert result.is_error
    assert _text(result).startswith("INVALID_ARGUMENTS: scoped_echo needs `project`")
    assert HINT in _text(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["project", "project_id"])
async def test_given_project_or_alias_passes_through(key: str):
    result = await _call(_server(scoped_echo), "scoped_echo", {"note": "plan", key: "main"})

    assert not result.is_error
    assert _text(result) == "main:plan"


@pytest.mark.asyncio
async def test_tool_with_optional_project_is_untouched():
    result = await _call(_server(unscoped_echo), "unscoped_echo", {"note": "plan"})

    assert not result.is_error
    assert _text(result) == "None:plan"


@pytest.mark.asyncio
async def test_bm_search_keeps_its_own_scope_refusal():
    result = await _call(_server(tools.bm_search), "bm_search", {"query": "plan"})

    assert result.is_error
    assert "Search needs an explicit scope" in _text(result)
    assert "needs `project`" not in _text(result)


@pytest.mark.asyncio
async def test_unknown_tool_is_reported_by_fastmcp():
    result = await _call(_server(scoped_echo), "no_such_tool", {})

    assert result.is_error
    assert "no_such_tool" in _text(result)


def test_basic_memory_server_registers_the_middleware():
    assert any(isinstance(middleware, MissingProjectMiddleware) for middleware in mcp.middleware)


@pytest.mark.asyncio
async def test_basic_memory_server_refuses_missing_project():
    async with Client(mcp) as client:
        result = await client.call_tool("bm_list", {"path": "/"}, raise_on_error=False)

    assert result.is_error
    assert _text(result).startswith("INVALID_ARGUMENTS: bm_list needs `project`")
    assert HINT in _text(result)
