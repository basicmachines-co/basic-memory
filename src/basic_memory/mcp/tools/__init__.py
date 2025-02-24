"""MCP tools for Basic Memory.

This package provides the complete set of tools for interacting with
Basic Memory through the MCP protocol. Importing this module registers
all tools with the MCP server.
"""

# Import tools to register them with MCP
from basic_memory.mcp.tools.resource import read_resource
from basic_memory.mcp.tools.memory import build_context, recent_activity
from basic_memory.mcp.tools.notes import read_note, write_note
from basic_memory.mcp.tools.search import search

from basic_memory.mcp.tools.knowledge import (
    delete_entities,
    get_entity,
    get_entities,
)

__all__ = [
    # Knowledge graph tools
    "delete_entities",
    "get_entity",
    "get_entities",
    # Search tools
    "search",
    # memory tools
    "build_context",
    "recent_activity",
    # notes
    "read_note",
    "write_note",
    # files
    "read_resource",
]
