"""Embedded UI tools using the MCP-UI Python SDK."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastmcp import Context
from mcp.types import ContentBlock, TextContent

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools.read_note import read_note
from basic_memory.mcp.tools.search import search_notes
from basic_memory.mcp.ui.sdk import MissingMCPUIServerError, build_embedded_ui_resource


def _text_block(message: str) -> List[ContentBlock]:
    return [TextContent(type="text", text=message)]


@mcp.tool(
    description="Search notes and return an embedded MCP-UI resource (raw HTML).",
    output_schema=None,
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
async def search_notes_ui(
    query: str,
    project: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
    search_type: Optional[str] = None,
    note_types: List[str] | None = None,
    entity_types: List[str] | None = None,
    after_date: Optional[str] = None,
    metadata_filters: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
    status: Optional[str] = None,
    context: Context | None = None,
) -> List[ContentBlock]:
    """Return a search results UI as an embedded MCP-UI resource."""
    result = await search_notes(
        query=query,
        project=project,
        page=page,
        page_size=page_size,
        search_type=search_type,
        output_format="json",
        note_types=note_types,
        entity_types=entity_types,
        after_date=after_date,
        metadata_filters=metadata_filters,
        tags=tags,
        status=status,
        context=context,
    )

    if isinstance(result, str):
        return _text_block(result)

    render_data = {
        "toolInput": {
            "query": query,
            "search_type": search_type,
            "page": page,
            "page_size": page_size,
        },
        "toolOutput": result,
    }

    try:
        resource = build_embedded_ui_resource(
            uri="ui://basic-memory/search-results/mcp-ui-sdk",
            html_filename="search-results-mcp-ui.html",
            render_data=render_data,
            preferred_frame_size=["100%", "540px"],
            metadata={"basic-memory.ui-variant": "mcp-ui-sdk"},
        )
    except MissingMCPUIServerError as exc:
        return _text_block(str(exc))

    return [resource]


@mcp.tool(
    description="Read a note and return an embedded MCP-UI resource (raw HTML).",
    output_schema=None,
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
async def read_note_ui(
    identifier: str,
    project: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
    context: Context | None = None,
) -> List[ContentBlock]:
    """Return a note preview UI as an embedded MCP-UI resource."""
    content = await read_note(
        identifier=identifier,
        project=project,
        page=page,
        page_size=page_size,
        output_format="text",
        context=context,
    )

    render_data = {
        "toolInput": {
            "identifier": identifier,
            "page": page,
            "page_size": page_size,
        },
        "toolOutput": content,
    }

    try:
        resource = build_embedded_ui_resource(
            uri="ui://basic-memory/note-preview/mcp-ui-sdk",
            html_filename="note-preview-mcp-ui.html",
            render_data=render_data,
            preferred_frame_size=["100%", "640px"],
            metadata={"basic-memory.ui-variant": "mcp-ui-sdk"},
        )
    except MissingMCPUIServerError as exc:
        return _text_block(str(exc))

    return [resource]
