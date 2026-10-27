"""An isolated transport harness for shared CLI implementations, not the public registry.

The shell CLI still calls these functions directly. Existing regression tests for
that implementation keep their serialization coverage here; public MCP surface
and workflow tests use the production server and bm_* names.
"""

import pytest
from fastmcp import FastMCP

import basic_memory.mcp.prompts  # noqa: F401
import basic_memory.mcp.resources  # noqa: F401
from basic_memory.mcp import tools
from basic_memory.mcp.client_info import MCPClientInfoMiddleware
from basic_memory.mcp.empty_results import EmptyListResultMiddleware
from basic_memory.mcp.server import mcp


@pytest.fixture
def implementation_server(config_manager, search_service) -> FastMCP:
    server = FastMCP("shared-implementation-tests", providers=[mcp])
    server.add_middleware(MCPClientInfoMiddleware())
    server.add_middleware(EmptyListResultMiddleware())
    for name in tools.__all__:
        if not name.startswith("bm_"):
            server.tool(getattr(tools, name), name=name)
    return server
