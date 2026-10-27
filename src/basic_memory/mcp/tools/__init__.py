"""MCP tools for Basic Memory.

This package provides the complete set of tools for interacting with
Basic Memory through the MCP protocol. Importing this module registers
all tools with the MCP server.
"""

# Shared implementations used by the CLI and the bm_* tools; these do not register MCP tools.
from basic_memory.mcp.tools.basic_memory_diagnostics import basic_memory_diagnostics
from basic_memory.mcp.tools.delete_note import delete_note
from basic_memory.mcp.tools.read_content import read_content
from basic_memory.mcp.tools.build_context import build_context
from basic_memory.mcp.tools.recent_activity import recent_activity
from basic_memory.mcp.tools.read_note import read_note

from basic_memory.mcp.tools.view_note import view_note
from basic_memory.mcp.tools.write_note import write_note
from basic_memory.mcp.tools.search import search_notes
from basic_memory.mcp.tools.list_directory import list_directory
from basic_memory.mcp.tools.edit_note import edit_note
from basic_memory.mcp.tools.move_note import move_note
from basic_memory.mcp.tools.workspaces import list_workspaces
from basic_memory.mcp.tools.project_management import (
    list_memory_projects,
    create_memory_project,
    delete_project,
)

# ChatGPT adapter implementations; Cloud owns their registration.
from basic_memory.mcp.tools.chatgpt_tools import search, fetch

# Shared POSIX implementations used by the shell commands.
from basic_memory.mcp.tools.posix_tools import cat, find, grep, ls, man, tail

# Schema tools
from basic_memory.mcp.tools.schema import schema_validate, schema_infer, schema_diff

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
    "basic_memory_diagnostics",
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
    "build_context",
    "cat",
    "create_memory_project",
    "delete_note",
    "delete_project",
    "edit_note",
    "fetch",
    "find",
    "grep",
    "list_directory",
    "list_memory_projects",
    "list_workspaces",
    "ls",
    "man",
    "move_note",
    "read_content",
    "read_note",
    "recent_activity",
    "schema_diff",
    "schema_infer",
    "schema_validate",
    "search",
    "search_notes",
    "tail",
    "view_note",
    "write_note",
]
