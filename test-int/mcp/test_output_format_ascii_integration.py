"""
Integration tests for ASCII/ANSI output formats in MCP tools.
"""

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_search_notes_ascii_output(mcp_server, app, test_project):
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "ASCII Note",
                "directory": "notes",
                "content": "# ASCII Note\n\nThis is a note for ASCII output.",
                "tags": "ascii,output",
            },
        )

        search_result = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "ASCII",
                "output_format": "ascii",
            },
        )

        assert len(search_result.content) == 1
        assert search_result.content[0].type == "text"
        text = search_result.content[0].text
        assert "Search results" in text
        assert "ASCII Note" in text
        assert "+" in text


@pytest.mark.asyncio
async def test_read_note_ascii_and_ansi_output(mcp_server, app, test_project):
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Color Note",
                "directory": "notes",
                "content": "# Color Note\n\nThis note is for ANSI output.",
                "tags": "ansi,output",
            },
        )

        ascii_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "Color Note",
                "output_format": "ascii",
            },
        )

        assert len(ascii_result.content) == 1
        ascii_text = ascii_result.content[0].text
        assert "Note preview" in ascii_text
        assert "# Color Note" in ascii_text

        ansi_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "Color Note",
                "output_format": "ansi",
            },
        )

        ansi_text = ansi_result.content[0].text
        assert "\x1b[" in ansi_text
