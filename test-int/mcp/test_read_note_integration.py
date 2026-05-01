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
                "directory": "test",
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
        assert f"{test_project.name}/test/test-note" in result_text  # permalink


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
                "directory": "_archive/articles",
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
        assert f"{test_project.name}/archive/articles/example-note" in write_text

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
        assert f"{test_project.name}/archive/articles/example-note" in result_text  # permalink


@pytest.mark.asyncio
async def test_read_note_by_project_id(mcp_server, app, test_project):
    """Read a note by passing project_id (UUID) instead of project name.

    Verifies the project_id parameter routes through get_project_client correctly
    in pure local mode (no cloud creds), where get_project_mode() would otherwise
    default unknown identifiers to CLOUD and break routing.
    """

    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "By ID Note",
                "directory": "test",
                "content": "# By ID Note\n\nLooked up by external_id.",
            },
        )

        # Read by external_id (UUID) instead of project name
        read_result = await client.call_tool(
            "read_note",
            {
                "project_id": test_project.external_id,
                "identifier": "By ID Note",
            },
        )

        assert len(read_result.content) == 1
        assert read_result.content[0].type == "text"
        result_text = read_result.content[0].text
        assert "# By ID Note" in result_text
        assert "Looked up by external_id." in result_text


@pytest.mark.asyncio
async def test_read_note_project_id_takes_precedence_over_name(mcp_server, app, test_project):
    """When project_id is passed alongside a wrong project name, project_id wins."""

    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Precedence Note",
                "directory": "test",
                "content": "# Precedence Note\n\nproject_id wins.",
            },
        )

        # Pass an obviously-wrong project name alongside the correct project_id.
        # If project_id takes precedence (as documented), the read still succeeds.
        read_result = await client.call_tool(
            "read_note",
            {
                "project": "this-project-does-not-exist",
                "project_id": test_project.external_id,
                "identifier": "Precedence Note",
            },
        )

        assert len(read_result.content) == 1
        result_text = read_result.content[0].text
        assert "# Precedence Note" in result_text
        assert "project_id wins." in result_text
