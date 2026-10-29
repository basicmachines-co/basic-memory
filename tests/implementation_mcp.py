"""An isolated transport harness for shared CLI implementations, not the public registry.

The shell CLI still calls these functions directly. Existing regression tests for
that implementation keep their serialization coverage here; public MCP surface
and workflow tests use the production server and bm_* names.
"""

import pytest
from fastmcp import FastMCP

import basic_memory.mcp.prompts  # noqa: F401
import basic_memory.mcp.resources  # noqa: F401
from basic_memory.config import ConfigManager
from basic_memory.services.search_service import SearchService
from basic_memory.mcp import tools
from basic_memory.mcp.client_info import MCPClientInfoMiddleware
from basic_memory.mcp.empty_results import EmptyListResultMiddleware
from basic_memory.mcp.server import mcp


IMPLEMENTATION_NAMES = (
    "build_context",
    "cat",
    "create_memory_project",
    "delete_project",
    "edit_note",
    "fetch",
    "find",
    "grep",
    "list_memory_projects",
    "list_workspaces",
    "ls",
    "man",
    "read_note",
    "recent_activity",
    "schema_diff",
    "schema_infer",
    "schema_validate",
    "search",
    "search_notes",
    "tail",
    "write_note",
)


@pytest.fixture
def implementation_server(config_manager: ConfigManager, search_service: SearchService) -> FastMCP:
    """Bind shared implementations on an isolated server for serialization tests."""
    server = FastMCP("shared-implementation-tests", providers=[mcp])
    server.add_middleware(MCPClientInfoMiddleware())
    server.add_middleware(EmptyListResultMiddleware())
    for name in IMPLEMENTATION_NAMES:
        server.tool(getattr(tools, name), name=name)
    return server
