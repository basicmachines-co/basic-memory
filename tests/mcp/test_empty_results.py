"""Empty list tool results still carry text content (#1634)."""

import json
from typing import Any

import pytest
from fastmcp import Client, FastMCP

from basic_memory.mcp.empty_results import EmptyListResultMiddleware
from basic_memory.mcp.server import mcp


def _server() -> FastMCP:
    server = FastMCP("empty-results")
    server.add_middleware(EmptyListResultMiddleware())

    @server.tool()
    async def rows(count: int = 0) -> str | list[dict[str, Any]]:
        return [{"row": index} for index in range(count)]

    return server


@pytest.mark.asyncio
async def test_empty_list_result_gets_one_json_text_block():
    async with Client(_server()) as client:
        result = await client.call_tool("rows", {"count": 0})

    assert result.structured_content == {"result": []}
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    assert json.loads(result.content[0].text) == []


@pytest.mark.asyncio
async def test_non_empty_list_result_is_unchanged():
    async with Client(_server()) as client:
        result = await client.call_tool("rows", {"count": 2})

    assert result.structured_content == {"result": [{"row": 0}, {"row": 1}]}
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    assert json.loads(result.content[0].text) == [{"row": 0}, {"row": 1}]


def test_basic_memory_server_registers_the_middleware():
    assert any(isinstance(middleware, EmptyListResultMiddleware) for middleware in mcp.middleware)
