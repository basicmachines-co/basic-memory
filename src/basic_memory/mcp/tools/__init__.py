"""MCP tools for Basic Memory.

This package provides the complete set of tools for interacting with
Basic Memory through the MCP protocol. Importing this module registers
all tools with the MCP server.
"""

# Import tools to register them with MCP
from basic_memory.mcp.tools.delete_note import delete_note
from basic_memory.mcp.tools.read_content import read_content
from basic_memory.mcp.tools.build_context import build_context
from basic_memory.mcp.tools.recent_activity import recent_activity
from basic_memory.mcp.tools.read_note import read_note
from basic_memory.mcp.tools.write_note import write_note
from basic_memory.mcp.tools.search import search
from basic_memory.mcp.tools.canvas import canvas

__all__ = [
    "build_context",
    "canvas",
    "delete_note",
    "read_content",
    "read_note",
    "recent_activity",
    "search",
    "write_note",
]
