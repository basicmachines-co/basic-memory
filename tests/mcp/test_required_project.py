"""Project scope is explicit at the MCP boundary, including single-project sessions."""

import ast
import importlib
import json
import re
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from basic_memory.mcp import tools
from basic_memory.mcp.refusals import Refusal
from basic_memory.mcp.tools import chatgpt_tools

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


@pytest.mark.asyncio
@pytest.mark.parametrize("name", SCOPED_TOOLS)
async def test_project_is_required_and_nonnullable_in_advertised_schema(name: str):
    server = FastMCP("project-scope")
    server.tool(getattr(tools, name))
    async with Client(server) as client:
        (tool,) = await client.list_tools()
    assert "project" in tool.input_schema["required"]
    project = tool.input_schema["properties"]["project"]
    assert project["type"] == "string"
    assert project["minLength"] == 1
    assert "default" not in project


@pytest.mark.asyncio
@pytest.mark.parametrize("name", SCOPED_TOOLS)
@pytest.mark.parametrize("scope", [{}, {"project": None}, {"project": ""}, {"project": "  "}])
async def test_invalid_scope_is_rejected_before_any_project_io(name: str, scope: dict[str, Any]):
    # No app, database or server lifespan: these calls must fail at validation,
    # even if a constrained server or a qualified identifier could infer a project.
    server = FastMCP("project-scope")
    server.tool(getattr(tools, name))
    async with Client(server) as client:
        result = await client.call_tool(name, {**SCOPED_TOOLS[name], **scope}, raise_on_error=False)
    assert result.is_error
    assert "project" in str(result.content)


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [{"query": "plan"}, {"query": "memory://research/plan"}])
async def test_single_project_search_requires_scope(arguments):
    with pytest.raises(ToolError, match="Search needs an explicit scope"):
        await tools.bm_search(**arguments)


@pytest.mark.asyncio
async def test_explicit_project_id_alias_reads_the_same_note(client, test_project):
    await tools.bm_write(identifier="notes/plan.md", content="# Plan", project=test_project.name)
    server = FastMCP("project-scope")
    server.tool(tools.bm_read)
    async with Client(server) as mcp_client:
        result = await mcp_client.call_tool(
            "bm_read",
            {"identifier": "notes/plan.md", "project_id": str(test_project.external_id)},
        )
    assert "# Plan" in str(result.content)


@pytest.mark.asyncio
async def test_discovery_and_bundled_manual_need_no_project(client, test_project):
    projects = await tools.bm_project(command="list", output_format="json")
    assert isinstance(projects, dict)
    assert test_project.name in {project["name"] for project in projects["projects"]}
    manual = await tools.bm_man(page="bm-read(3)")
    assert "bm_read" in str(manual)


@pytest.mark.asyncio
async def test_required_scope_keeps_same_named_folders_inside_selected_project(
    client, test_project, second_project
):
    await tools.bm_write(
        identifier="notes/plan.md", content="First project's plan", project=test_project.name
    )
    await tools.bm_write(
        identifier="notes/plan.md", content="Second project's plan", project=second_project.name
    )
    first = await tools.bm_read("notes/plan.md", project=test_project.name)
    second = await tools.bm_read("notes/plan.md", project=str(second_project.external_id))
    assert "First project's plan" in str(first) and "Second project's plan" not in str(first)
    assert "Second project's plan" in str(second) and "First project's plan" not in str(second)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["projects", "all_projects"])
async def test_search_explicit_multi_project_scope_is_preserved(client, test_project, scope):
    await tools.bm_write(
        identifier="notes/plan.md", content="uniquebenchscopeword", project=test_project.name
    )
    result = await tools.bm_search(
        query="uniquebenchscopeword",
        mode="text",
        output_format="json",
        projects=[test_project.name] if scope == "projects" else None,
        all_projects=scope == "all_projects",
    )
    assert isinstance(result, dict)
    assert result["results"]


@pytest.mark.asyncio
async def test_conflict_recovery_call_includes_scope_and_can_be_dispatched(client, test_project):
    await tools.bm_write(identifier="notes/plan.md", content="# Plan", project=test_project.name)
    with pytest.raises(Refusal) as conflict:
        await tools.bm_edit(
            identifier="notes/plan.md",
            command="append",
            content="tail",
            expected_checksum="stale",
            project=test_project.name,
        )
    hint = conflict.value.try_instead
    assert hint is not None
    call = ast.parse(hint.split(", then retry")[0], mode="eval").body
    assert isinstance(call, ast.Call)
    arguments = {argument.arg: ast.literal_eval(argument.value) for argument in call.keywords}
    assert arguments["project"] == test_project.name
    server = FastMCP("project-scope")
    server.tool(tools.bm_read)
    async with Client(server) as mcp_client:
        result = await mcp_client.call_tool("bm_read", arguments)
    assert "# Plan" in str(result.content)


@pytest.mark.asyncio
async def test_chatgpt_fetch_reports_unresolved_scope(monkeypatch):
    monkeypatch.setattr(chatgpt_tools, "is_openai_mcp_client", AsyncMock(return_value=True))
    monkeypatch.setattr(
        chatgpt_tools, "detect_project_from_identifier_prefix", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(chatgpt_tools, "resolve_project_parameter", AsyncMock(return_value=None))
    (result,) = await chatgpt_tools.fetch("Plan")
    assert json.loads(result["text"])["metadata"]["error"] == "INVALID_ARGUMENTS"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module_name", "prompt_name", "arguments"),
    [
        ("continue_conversation", "continue_conversation", {}),
        ("recent_activity", "recent_activity_prompt", {}),
        ("search", "search_prompt", {"query": "plan"}),
    ],
)
async def test_prompts_discover_before_calling_tools_when_no_project_resolves(
    monkeypatch, module_name, prompt_name, arguments
):
    module = importlib.import_module(f"basic_memory.mcp.prompts.{module_name}")
    monkeypatch.setattr(module, "resolve_project_parameter", AsyncMock(return_value=None))
    result = await getattr(module, prompt_name)(**arguments)
    assert "bm_project(command='list')" in result
    assert "explicit project" in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module_name", "prompt_name", "arguments"),
    [
        ("search", "search_prompt", {"query": "scopepromptnonce"}),
        ("continue_conversation", "continue_conversation", {"topic": "scopepromptnonce"}),
        ("continue_conversation", "continue_conversation", {"topic": "nomatchuniqueword"}),
        ("continue_conversation", "continue_conversation", {}),
        ("recent_activity", "recent_activity_prompt", {}),
    ],
)
async def test_every_saved_prompt_followup_call_carries_its_scope(
    client, test_project, module_name, prompt_name, arguments
):
    await tools.bm_write(
        identifier="notes/plan.md", content="# scopepromptnonce", project=test_project.name
    )
    module = importlib.import_module(f"basic_memory.mcp.prompts.{module_name}")
    result = await getattr(module, prompt_name)(project=test_project.name, **arguments)
    calls = re.findall(r"`(bm_(?:read|context|search|project)\([^`\n]*\))`", result)
    assert calls
    for example in calls:
        call = ast.parse(example, mode="eval").body
        assert isinstance(call, ast.Call)
        scope = [keyword.value for keyword in call.keywords if keyword.arg == "project"]
        assert len(scope) == 1, example
        assert ast.literal_eval(scope[0]) == test_project.name
