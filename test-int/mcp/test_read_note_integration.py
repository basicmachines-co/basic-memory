"""
Integration tests for read_note MCP tool.

Tests the full flow: MCP client -> MCP server -> FastAPI -> database
"""

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_read_note_after_write(mcp_server, app, test_project):
    """Test read_note after write_note using real database."""

    async with Client(mcp_server) as client:
        # First write a note
        write_result = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Test Note",
                "folder": "test",
                "content": "# Test Note\n\nThis is test content.",
                "tags": "test,integration",
            },
        )

        assert len(write_result.content) == 1
        assert write_result.content[0].type == "text"
        assert "Test Note.md" in write_result.content[0].text

        # Then read it back
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "Test Note",
            },
        )

        assert len(read_result.content) == 1
        assert read_result.content[0].type == "text"
        result_text = read_result.content[0].text

        # Should contain the note content and metadata
        assert "# Test Note" in result_text
        assert "This is test content." in result_text
        assert "test/test-note" in result_text  # permalink


@pytest.mark.asyncio
async def test_read_note_underscored_folder_by_permalink(mcp_server, app, test_project):
    """Test read_note with permalink from underscored folder.

    Reproduces bug #416: read_note fails to find notes when given permalinks
    from underscored folder names (e.g., _archive/, _drafts/), even though
    the permalink is copied directly from the note's YAML frontmatter.
    """

    async with Client(mcp_server) as client:
        # Create a note in an underscored folder
        write_result = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Example Note",
                "folder": "_archive/articles",
                "content": "# Example Note\n\nThis is a test note in an underscored folder.",
                "tags": "test,archive",
            },
        )

        assert len(write_result.content) == 1
        assert write_result.content[0].type == "text"
        write_text = write_result.content[0].text

        # Verify the file path includes the underscore
        assert "_archive/articles/Example Note.md" in write_text

        # Verify the permalink has underscores stripped (this is the expected behavior)
        assert "archive/articles/example-note" in write_text

        # Now try to read the note using the permalink (without underscores)
        # This is the exact scenario from the bug report - using the permalink
        # that was generated in the YAML frontmatter
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "archive/articles/example-note",  # permalink without underscores
            },
        )

        # This should succeed - the note should be found by its permalink
        assert len(read_result.content) == 1
        assert read_result.content[0].type == "text"
        result_text = read_result.content[0].text

        # Should contain the note content
        assert "# Example Note" in result_text
        assert "This is a test note in an underscored folder." in result_text
        assert "archive/articles/example-note" in result_text  # permalink
