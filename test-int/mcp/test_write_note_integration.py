"""
Integration tests for write_note MCP tool.

Tests various scenarios including note creation, content formatting,
tag handling, and error conditions.
"""

from textwrap import dedent

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_write_note_create_new_note(mcp_server, app):
    """Test creating a simple note with basic content."""

    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "write_note",
            {
                "title": "Simple Note",
                "folder": "basic",
                "content": "# Simple Note\n\nThis is a simple note for testing.",
                "tags": "simple,test",
            },
        )

        assert len(result) == 1
        assert result[0].type == "text"
        assert (
            result[0].text
            == dedent(
                """
            # Created note
            file_path: basic/Simple Note.md
            permalink: basic/simple-note
            checksum: ff5ae789
            
            ## Tags
            - simple, test
            """
            ).strip()
        )
