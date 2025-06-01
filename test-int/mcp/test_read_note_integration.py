"""
Integration tests for read_note MCP tool.

Tests the full flow: MCP client -> MCP server -> FastAPI -> database
"""

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_read_note_after_write(mcp_server, app):
    """Test read_note after write_note using real database."""

    async with Client(mcp_server) as client:
        # First write a note
        write_result = await client.call_tool(
            "write_note",
            {
                "title": "Test Note",
                "folder": "test",
                "content": "# Test Note\n\nThis is test content.",
                "tags": "test,integration",
            },
        )

        assert len(write_result) == 1
        assert write_result[0].type == "text"
        assert "Test Note.md" in write_result[0].text

        # Then read it back
        read_result = await client.call_tool(
            "read_note",
            {
                "identifier": "Test Note",
            },
        )

        assert len(read_result) == 1
        assert read_result[0].type == "text"
        result_text = read_result[0].text

        # Should contain the note content and metadata
        assert "# Test Note" in result_text
        assert "This is test content." in result_text
        assert "test/test-note" in result_text  # permalink
