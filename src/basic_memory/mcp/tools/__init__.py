"""MCP tools for Basic Memory.

This package provides the complete set of tools for interacting with
Basic Memory through the MCP protocol. Importing this module registers
all tools with the MCP server.
"""

# Shared implementations used by the CLI and the bm_* tools; these do not register MCP tools.
from basic_memory.mcp.tools.search import search_notes as search_notes

# Shared POSIX implementations used by the shell commands.
from basic_memory.mcp.tools.posix_tools import (
    cat as cat,
    find as find,
    grep as grep,
    ls as ls,
    man as man,
    tail as tail,
)

# 1.0 tool surface (SPEC-35.2, #1827).
from basic_memory.mcp.tools.context import bm_context
from basic_memory.mcp.tools.delete import bm_delete
from basic_memory.mcp.tools.edit import bm_edit
from basic_memory.mcp.tools.find_files import bm_find
from basic_memory.mcp.tools.listing import bm_list
from basic_memory.mcp.tools.manual import bm_man
from basic_memory.mcp.tools.move import bm_move
from basic_memory.mcp.tools.note_schemas import bm_schema

from basic_memory.mcp.tools.projects import bm_project, bm_project_delete
from basic_memory.mcp.tools.ranked_search import bm_search
from basic_memory.mcp.tools.read import bm_read
from basic_memory.mcp.tools.write import bm_write

__all__ = [
    "bm_context",
    "bm_delete",
    "bm_edit",
    "bm_find",
    "bm_list",
    "bm_man",
    "bm_move",
    "bm_project",
    "bm_project_delete",
    "bm_read",
    "bm_schema",
    "bm_search",
    "bm_write",
]
