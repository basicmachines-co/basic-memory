"""Release notes MCP tool."""

from pathlib import Path

from basic_memory.mcp.server import mcp


@mcp.tool(
    "release_notes",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
def release_notes() -> str:
    """Return the latest product release notes for optional user review."""
    content_path = Path(__file__).parent.parent / "resources" / "release_notes.md"
    return content_path.read_text(encoding="utf-8")
