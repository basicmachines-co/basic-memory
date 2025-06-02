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
from basic_memory.mcp.tools.search import search_notes
from basic_memory.mcp.tools.canvas import canvas
from basic_memory.mcp.tools.list_directory import list_directory
from basic_memory.mcp.tools.edit_note import edit_note
from basic_memory.mcp.tools.move_note import move_note
from basic_memory.mcp.tools.project_management import (
    list_projects,
    switch_project,
    get_current_project,
    set_default_project,
    create_project,
    delete_project,
)

__all__ = [
    "build_context",
    "canvas",
    "create_project",
    "delete_note",
    "delete_project",
    "edit_note",
    "get_current_project",
    "list_directory",
    "list_projects",
    "move_note",
    "read_content",
    "read_note",
    "recent_activity",
    "search_notes",
    "set_default_project",
    "switch_project",
    "write_note",
]
