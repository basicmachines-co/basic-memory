"""Cloud information MCP tool."""

from pathlib import Path

from basic_memory.mcp.server import mcp


@mcp.tool(
    "cloud_info",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
def cloud_info() -> str:
    """Return optional Basic Memory Cloud information and setup guidance."""
    # Import here to avoid pulling CLI promo machinery (analytics, rich, config)
    # into the MCP server import graph at module load.
    from basic_memory.cli.promo import OSS_DISCOUNT_CODE

    content_path = Path(__file__).parent.parent / "resources" / "cloud_info.md"
    content = content_path.read_text(encoding="utf-8")
    # The bundled markdown carries a template placeholder so the promo code has
    # one source of truth (cli.promo); substitute before it reaches users.
    return content.replace("{{OSS_DISCOUNT_CODE}}", OSS_DISCOUNT_CODE)
