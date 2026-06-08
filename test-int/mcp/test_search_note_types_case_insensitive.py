"""Bughunt: search_notes note_types filter is documented case-insensitive but
fails to match capitalized frontmatter `type` values.
"""

import json
from typing import Any

import pytest
from fastmcp import Client


def _json(tool_result) -> Any:
    assert len(tool_result.content) == 1
    assert tool_result.content[0].type == "text"
    return json.loads(tool_result.content[0].text)


@pytest.mark.asyncio
async def test_note_types_filter_is_case_insensitive(mcp_server, app, test_project):
    async with Client(mcp_server) as client:
        content = (
            "---\n"
            "title: Capitalized Type Note\n"
            "type: Chapter\n"
            "---\n"
            "# Capitalized Type Note\n\nuniqtoken99 body text\n"
        )
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Capitalized Type Note",
                "directory": "types",
                "content": content,
            },
        )
        plain = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "uniqtoken99",
                "search_type": "text",
                "output_format": "json",
            },
        )
        plain_data = _json(plain)
        assert plain_data["results"], "note not indexed at all"
        res = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "uniqtoken99",
                "search_type": "text",
                "note_types": ["Chapter"],
                "output_format": "json",
            },
        )
        data = _json(res)
        titles = [r["title"] for r in data["results"]]
        assert "Capitalized Type Note" in titles, (
            "note_types filter is documented case-insensitive but did not match the "
            f"capitalized frontmatter type 'Chapter'. results={data}"
        )


@pytest.mark.asyncio
async def test_note_types_lowercase_control(mcp_server, app, test_project):
    """Control: a lowercase frontmatter type DOES match (proves the bug is casing)."""
    async with Client(mcp_server) as client:
        content = (
            "---\n"
            "title: Lowercase Type Note\n"
            "type: chapter\n"
            "---\n"
            "# Lowercase Type Note\n\nuniqtoken88 body text\n"
        )
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Lowercase Type Note",
                "directory": "types",
                "content": content,
            },
        )
        res = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "uniqtoken88",
                "search_type": "text",
                "note_types": ["Chapter"],
                "output_format": "json",
            },
        )
        data = _json(res)
        titles = [r["title"] for r in data["results"]]
        assert "Lowercase Type Note" in titles, data
