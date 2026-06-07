"""Integration tests locking in write_note note_type behavior (Issue #875).

These exercise the real MCP harness (no mocks) to confirm that:
- content frontmatter ``type:`` is persisted as the note type and is searchable
  via the ``note_types`` filter;
- overwriting a note with a different content ``type:`` flips the persisted type.

The CLI ``--type`` passthrough is covered separately in
``test-int/cli/test_cli_tool_write_note_type_integration.py``.
"""

import json
from textwrap import dedent
from typing import Any

import pytest
from fastmcp import Client


def _json_content(tool_result) -> dict[str, Any]:
    """Parse a FastMCP tool result content block into a JSON object."""
    assert len(tool_result.content) == 1
    assert tool_result.content[0].type == "text"
    payload = json.loads(tool_result.content[0].text)  # pyright: ignore [reportAttributeAccessIssue]
    assert isinstance(payload, dict)
    return payload


@pytest.mark.asyncio
async def test_write_note_content_type_is_persisted_and_searchable(mcp_server, app, test_project):
    """Content frontmatter ``type:`` persists and is found by the note_types filter."""
    note = dedent("""
        ---
        title: Session Log
        type: session
        ---

        # Session Log

        SessionTypeToken content body.
    """).strip()

    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Session Log",
                "directory": "logs",
                "content": note,
                "output_format": "json",
            },
        )

        # The persisted frontmatter should report the content-declared type.
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "logs/session-log",
                "include_frontmatter": True,
                "output_format": "json",
            },
        )
        read_payload = _json_content(read_result)
        assert read_payload["frontmatter"]["type"] == "session"

        # And the note_types filter must return it (and only it for this token).
        search_result = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "SessionTypeToken",
                "search_type": "text",
                "note_types": ["session"],
                "output_format": "json",
            },
        )
        search_payload = _json_content(search_result)
        permalinks = {item["permalink"] for item in search_payload["results"]}
        assert any(p.endswith("logs/session-log") for p in permalinks)


@pytest.mark.asyncio
async def test_write_note_overwrite_flips_persisted_type(mcp_server, app, test_project):
    """Overwriting with a different content ``type:`` flips the persisted note type."""
    session_note = dedent("""
        ---
        title: Type Flip
        type: session
        ---

        # Type Flip

        Original session body.
    """).strip()

    schema_note = dedent("""
        ---
        title: Type Flip
        type: schema
        ---

        # Type Flip

        Replacement schema body.
    """).strip()

    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Type Flip",
                "directory": "flip",
                "content": session_note,
                "output_format": "json",
            },
        )

        before = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "flip/type-flip",
                "include_frontmatter": True,
                "output_format": "json",
            },
        )
        assert _json_content(before)["frontmatter"]["type"] == "session"

        # Overwrite with a different content type.
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Type Flip",
                "directory": "flip",
                "content": schema_note,
                "overwrite": True,
                "output_format": "json",
            },
        )

        after = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "flip/type-flip",
                "include_frontmatter": True,
                "output_format": "json",
            },
        )
        assert _json_content(after)["frontmatter"]["type"] == "schema"
