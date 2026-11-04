"""Bundled MCP resources for Basic Memory.

The distributed skills are not imported here: they need the installed
distribution, and `basic_memory.mcp.resources.skills` registers them when the
server entrypoint imports it.
"""

from basic_memory.mcp.resources.man import manual_index, manual_page
from basic_memory.mcp.resources.notes import note_resource
from basic_memory.mcp.resources.project_info import project_info

__all__ = ["manual_index", "manual_page", "note_resource", "project_info"]
