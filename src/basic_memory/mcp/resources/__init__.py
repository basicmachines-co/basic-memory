"""Bundled MCP resources for Basic Memory."""

from basic_memory.mcp.resources.man import manual_index, manual_page
from basic_memory.mcp.resources.notes import note_resource
from basic_memory.mcp.resources.project_info import project_info
from basic_memory.mcp.resources.skills import skills_provider

__all__ = ["manual_index", "manual_page", "note_resource", "project_info", "skills_provider"]
