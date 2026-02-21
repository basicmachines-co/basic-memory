"""Cloud information MCP tool."""

from pathlib import Path

from basic_memory.mcp.server import mcp


@mcp.tool(
    "cloud_info",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
def cloud_info() -> str:
    """Return optional Basic Memory Cloud information and setup guidance."""
    content_path = Path(__file__).parent.parent / "resources" / "cloud_info.md"
    return content_path.read_text(encoding="utf-8")
