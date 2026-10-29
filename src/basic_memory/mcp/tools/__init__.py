"""MCP tools for Basic Memory.

This package provides the complete set of tools for interacting with
Basic Memory through the MCP protocol. Importing this module registers
all tools with the MCP server.
"""

# Shared implementations used by the CLI and the bm_* tools; these do not register MCP tools.
from basic_memory.mcp.tools.build_context import build_context as build_context
from basic_memory.mcp.tools.recent_activity import recent_activity as recent_activity
from basic_memory.mcp.tools.read_note import read_note as read_note

from basic_memory.mcp.tools.write_note import write_note as write_note
from basic_memory.mcp.tools.search import search_notes as search_notes
from basic_memory.mcp.tools.edit_note import edit_note as edit_note
from basic_memory.mcp.tools.workspaces import list_workspaces as list_workspaces
from basic_memory.mcp.tools.project_management import (
    list_memory_projects as list_memory_projects,
    create_memory_project as create_memory_project,
    delete_project as delete_project,
)

# ChatGPT adapter implementations; Cloud owns their registration.
from basic_memory.mcp.tools.chatgpt_tools import search as search, fetch as fetch

# Shared POSIX implementations used by the shell commands.
from basic_memory.mcp.tools.posix_tools import (
    cat as cat,
    find as find,
    grep as grep,
    ls as ls,
    man as man,
    tail as tail,
)

# Schema tools
from basic_memory.mcp.tools.schema import (
    schema_validate as schema_validate,
    schema_infer as schema_infer,
    schema_diff as schema_diff,
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
