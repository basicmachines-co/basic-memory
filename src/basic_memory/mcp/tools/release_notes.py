"""Release notes MCP tool."""

from basic_memory.mcp.resources.discovery import load_discovery_resource
from basic_memory.mcp.server import mcp


@mcp.tool(
    "release_notes",
    title="Release Notes",
    tags={"cloud"},
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
def release_notes() -> str:
    """Return the latest product release notes for optional user review."""
    return load_discovery_resource("release_notes.md")
