"""UI resources for MCP Apps integration."""

from basic_memory.mcp.server import mcp
from basic_memory.mcp.ui import load_html, load_variant_html

# FastMCP's MIME type validator currently accepts only type/subtype, so we
# use text/html here. MCP Apps hosts typically expect text/html;profile=mcp-app.
MIME_TYPE = "text/html"


@mcp.resource(
    uri="ui://basic-memory/search-results",
    name="Basic Memory Search Results",
    description="Search results UI for Basic Memory tools.",
    mime_type=MIME_TYPE,
)
def search_results_ui() -> str:
    return load_variant_html("search-results")


@mcp.resource(
    uri="ui://basic-memory/note-preview",
    name="Basic Memory Note Preview",
    description="Note preview UI for Basic Memory read_note tool.",
    mime_type=MIME_TYPE,
)
def note_preview_ui() -> str:
    return load_variant_html("note-preview")


# Variant-specific resource URIs for bakeoff comparisons.
@mcp.resource(
    uri="ui://basic-memory/search-results/vanilla",
    name="Basic Memory Search Results (Vanilla)",
    description="Vanilla HTML search results UI.",
    mime_type=MIME_TYPE,
)
def search_results_ui_vanilla() -> str:
    return load_html("search-results-vanilla.html")


@mcp.resource(
    uri="ui://basic-memory/search-results/tool-ui",
    name="Basic Memory Search Results (Tool UI)",
    description="Tool UI styled search results UI.",
    mime_type=MIME_TYPE,
)
def search_results_ui_tool_ui() -> str:
    return load_html("search-results-tool-ui.html")


@mcp.resource(
    uri="ui://basic-memory/search-results/mcp-ui",
    name="Basic Memory Search Results (MCP UI)",
    description="MCP UI styled search results UI.",
    mime_type=MIME_TYPE,
)
def search_results_ui_mcp_ui() -> str:
    return load_html("search-results-mcp-ui.html")


@mcp.resource(
    uri="ui://basic-memory/note-preview/vanilla",
    name="Basic Memory Note Preview (Vanilla)",
    description="Vanilla HTML note preview UI.",
    mime_type=MIME_TYPE,
)
def note_preview_ui_vanilla() -> str:
    return load_html("note-preview-vanilla.html")


@mcp.resource(
    uri="ui://basic-memory/note-preview/tool-ui",
    name="Basic Memory Note Preview (Tool UI)",
    description="Tool UI styled note preview UI.",
    mime_type=MIME_TYPE,
)
def note_preview_ui_tool_ui() -> str:
    return load_html("note-preview-tool-ui.html")


@mcp.resource(
    uri="ui://basic-memory/note-preview/mcp-ui",
    name="Basic Memory Note Preview (MCP UI)",
    description="MCP UI styled note preview UI.",
    mime_type=MIME_TYPE,
)
def note_preview_ui_mcp_ui() -> str:
    return load_html("note-preview-mcp-ui.html")
