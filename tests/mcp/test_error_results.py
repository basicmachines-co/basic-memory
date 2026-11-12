"""Tool errors carry structured content that fits the tool's output schema (#2094)."""

from typing import Any

import pytest
from fastmcp import Client, FastMCP

# Importing the tools package registers every bm_* tool on the server.
import basic_memory.mcp.tools  # noqa: F401
from basic_memory.mcp.error_results import (
    ToolErrorResultMiddleware,
    wrapped_result_accepts_text,
)
from basic_memory.mcp.missing_project import MissingProjectMiddleware
from basic_memory.mcp.refusals import refusal
from basic_memory.mcp.server import mcp

EXISTS = "ALREADY_EXISTS: A note already exists at 'notes/a.md'. Nothing was written."


def _server() -> FastMCP:
    server = FastMCP("error-results")
    server.add_middleware(ToolErrorResultMiddleware())
    server.add_middleware(MissingProjectMiddleware())

    @server.tool()
    async def write(project: str, fail: bool = True) -> str | dict[str, Any]:
        if fail:
            raise refusal(
                "ALREADY_EXISTS", "A note already exists at 'notes/a.md'. Nothing was written."
            )
        return "Created notes/a.md"

    @server.tool()
    async def record(fail: bool = True) -> dict[str, Any]:
        if fail:
            raise refusal("NOT_FOUND", "No such record.")
        return {"ok": True}

    return server


@pytest.mark.asyncio
async def test_refusal_keeps_its_text_and_gains_matching_structured_content():
    async with Client(_server()) as client:
        result = await client.call_tool_mcp("write", {"project": "main"})

    assert result.is_error is True
    assert [block.text for block in result.content] == [EXISTS]
    assert result.structured_content == {"result": EXISTS}


@pytest.mark.asyncio
async def test_refusal_raised_by_another_middleware_gets_structured_content():
    async with Client(_server()) as client:
        result = await client.call_tool_mcp("write", {})

    assert result.is_error is True
    assert result.structured_content is not None
    assert result.structured_content["result"].startswith(
        "INVALID_ARGUMENTS: write needs `project`"
    )


@pytest.mark.asyncio
async def test_error_from_a_tool_whose_schema_cannot_hold_text_is_unchanged():
    async with Client(_server()) as client:
        result = await client.call_tool_mcp("record", {})

    assert result.is_error is True
    assert [block.text for block in result.content] == ["NOT_FOUND: No such record."]
    assert result.structured_content is None


@pytest.mark.asyncio
async def test_successful_calls_are_unchanged():
    async with Client(_server()) as client:
        result = await client.call_tool_mcp("write", {"project": "main", "fail": False})

    assert result.is_error is False
    assert result.structured_content == {"result": "Created notes/a.md"}


def test_wrapped_result_accepts_text():
    wrapped = {"x-fastmcp-wrap-result": True, "properties": {"result": {"type": "string"}}}
    either = {
        "x-fastmcp-wrap-result": True,
        "properties": {"result": {"anyOf": [{"type": "string"}, {"type": "object"}]}},
    }
    objects_only = {"x-fastmcp-wrap-result": True, "properties": {"result": {"type": "object"}}}

    assert wrapped_result_accepts_text(wrapped)
    assert wrapped_result_accepts_text(either)
    assert not wrapped_result_accepts_text(objects_only)
    assert not wrapped_result_accepts_text({"type": "object", "properties": {}})
    assert not wrapped_result_accepts_text(None)


@pytest.mark.asyncio
async def test_every_basic_memory_output_schema_holds_an_error_message():
    """The middleware helps only if the real bm_* schemas accept the message.

    A tool with no output schema (bm_read returns content blocks) gives a client
    nothing to validate, so its errors need no structured content.
    """
    schemas = {tool.name: tool.output_schema for tool in await mcp.list_tools()}
    with_schema = {name: schema for name, schema in schemas.items() if schema}

    assert "bm_write" in with_schema
    assert [
        name for name, schema in with_schema.items() if not wrapped_result_accepts_text(schema)
    ] == []


def test_basic_memory_server_registers_the_middleware_outside_missing_project():
    kinds = [type(middleware) for middleware in mcp.middleware]

    assert kinds.index(ToolErrorResultMiddleware) < kinds.index(MissingProjectMiddleware)
