"""Cloud information MCP tool."""

from basic_memory.mcp.resources.discovery import load_discovery_resource
from basic_memory.mcp.server import mcp


@mcp.tool(
    "cloud_info",
    title="Cloud Info",
    tags={"cloud"},
    annotations={"title": "Cloud Info", "readOnlyHint": True, "openWorldHint": False},
)
def cloud_info() -> str:
    """Return optional Basic Memory Cloud information and setup guidance."""
    return load_discovery_resource("cloud_info.md")
