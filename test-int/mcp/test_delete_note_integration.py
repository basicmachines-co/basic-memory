"""
Integration tests for delete_note MCP tool.

Tests the complete delete note workflow: MCP client -> MCP server -> FastAPI -> database
"""

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_delete_note_by_title(mcp_server, app, test_project):
    """Test deleting a note by its title."""

    async with Client(mcp_server) as client:
        # First create a note
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Note to Delete",
                "folder": "test",
                "content": "# Note to Delete\n\nThis note will be deleted.",
                "tags": "test,delete",
            },
        )

        # Verify the note exists by reading it
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "Note to Delete",
            },
        )
        assert len(read_result.content) == 1
        assert "Note to Delete" in read_result.content[0].text

        # Delete the note by title
        delete_result = await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": "Note to Delete",
            },
        )

        # Should return True for successful deletion
        assert len(delete_result.content) == 1
        assert delete_result.content[0].type == "text"
        assert "true" in delete_result.content[0].text.lower()

        # Verify the note no longer exists
        read_after_delete = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "Note to Delete",
            },
        )

        # Should return helpful "Note Not Found" message instead of the actual note
        assert len(read_after_delete.content) == 1
        result_text = read_after_delete.content[0].text
        assert "Note Not Found" in result_text
        assert "Note to Delete" in result_text


@pytest.mark.asyncio
async def test_delete_note_by_permalink(mcp_server, app, test_project):
    """Test deleting a note by its permalink."""

    async with Client(mcp_server) as client:
        # Create a note
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Permalink Delete Test",
                "folder": "tests",
                "content": "# Permalink Delete Test\n\nTesting deletion by permalink.",
                "tags": "test,permalink",
            },
        )

        # Delete the note by permalink
        delete_result = await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": "tests/permalink-delete-test",
            },
        )

        # Should return True for successful deletion
        assert len(delete_result.content) == 1
        assert "true" in delete_result.content[0].text.lower()

        # Verify the note no longer exists by searching
        search_result = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "Permalink Delete Test",
            },
        )

        # Should have no results
        assert (
            '"results": []' in search_result.content[0].text
            or '"results":[]' in search_result.content[0].text
        )


@pytest.mark.asyncio
async def test_delete_note_with_observations_and_relations(mcp_server, app, test_project):
    """Test deleting a note that has observations and relations."""

    async with Client(mcp_server) as client:
        # Create a complex note with observations and relations
        complex_content = """# Project Management System

This is a comprehensive project management system.

## Observations
- [feature] Task tracking functionality
- [feature] User authentication system
- [tech] Built with Python and Flask
- [status] Currently in development

## Relations
- depends_on [[Database Schema]]
- implements [[User Stories]]
- part_of [[Main Application]]

The system handles multiple projects and users."""

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Project Management System",
                "folder": "projects",
                "content": complex_content,
                "tags": "project,management,system",
            },
        )

        # Verify the note exists and has content
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "Project Management System",
            },
        )
        assert len(read_result.content) == 1
        result_text = read_result.content[0].text
        assert "Task tracking functionality" in result_text
        assert "depends_on" in result_text

        # Delete the complex note
        delete_result = await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": "projects/project-management-system",
            },
        )

        # Should return True for successful deletion
        assert "true" in delete_result.content[0].text.lower()

        # Verify the note and all its components are deleted
        read_after_delete_2 = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "Project Management System",
            },
        )

        # Should return "Note Not Found" message
        assert len(read_after_delete_2.content) == 1
        result_text = read_after_delete_2.content[0].text
        assert "Note Not Found" in result_text
        assert "Project Management System" in result_text


@pytest.mark.asyncio
async def test_delete_note_special_characters_in_title(mcp_server, app, test_project):
    """Test deleting notes with special characters in the title."""

    async with Client(mcp_server) as client:
        # Create notes with special characters
        special_titles = [
            "Note with spaces",
            "Note-with-dashes",
            "Note_with_underscores",
            "Note (with parentheses)",
            "Note & Symbols!",
        ]

        # Create all the notes
        for title in special_titles:
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": title,
                    "folder": "special",
                    "content": f"# {title}\n\nContent for {title}",
                    "tags": "special,characters",
                },
            )

        # Delete each note by title
        for title in special_titles:
            delete_result = await client.call_tool(
                "delete_note",
                {
                    "project": test_project.name,
                    "identifier": title,
                },
            )

            # Should return True for successful deletion
            assert "true" in delete_result.content[0].text.lower(), (
                f"Failed to delete note: {title}"
            )

            # Verify the note is deleted
            read_after_delete = await client.call_tool(
                "read_note",
                {
                    "project": test_project.name,
                    "identifier": title,
                },
            )

            # Should return "Note Not Found" message
            assert len(read_after_delete.content) == 1
            result_text = read_after_delete.content[0].text
            assert "Note Not Found" in result_text
            assert title in result_text


@pytest.mark.asyncio
async def test_delete_nonexistent_note(mcp_server, app, test_project):
    """Test attempting to delete a note that doesn't exist."""

    async with Client(mcp_server) as client:
        # Try to delete a note that doesn't exist
        delete_result = await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": "Nonexistent Note",
            },
        )

        # Should return False for unsuccessful deletion
        assert len(delete_result.content) == 1
        assert "false" in delete_result.content[0].text.lower()


@pytest.mark.asyncio
async def test_delete_note_by_file_path(mcp_server, app, test_project):
    """Test deleting a note using its file path."""

    async with Client(mcp_server) as client:
        # Create a note
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "File Path Delete",
                "folder": "docs",
                "content": "# File Path Delete\n\nTesting deletion by file path.",
                "tags": "test,filepath",
            },
        )

        # Try to delete using the file path (should work as an identifier)
        delete_result = await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": "docs/File Path Delete.md",
            },
        )

        # Should return True for successful deletion
        assert "true" in delete_result.content[0].text.lower()

        # Verify deletion
        read_after_delete = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "File Path Delete",
            },
        )

        # Should return "Note Not Found" message
        assert len(read_after_delete.content) == 1
        result_text = read_after_delete.content[0].text
        assert "Note Not Found" in result_text
        assert "File Path Delete" in result_text


@pytest.mark.asyncio
async def test_delete_note_case_insensitive(mcp_server, app, test_project):
    """Test that note deletion is case insensitive for titles."""

    async with Client(mcp_server) as client:
        # Create a note with mixed case
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "CamelCase Note Title",
                "folder": "test",
                "content": "# CamelCase Note Title\n\nTesting case sensitivity.",
                "tags": "test,case",
            },
        )

        # Try to delete with different case
        delete_result = await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": "camelcase note title",
            },
        )

        # Should return True for successful deletion
        assert "true" in delete_result.content[0].text.lower()


@pytest.mark.asyncio
async def test_delete_multiple_notes_sequentially(mcp_server, app, test_project):
    """Test deleting multiple notes in sequence."""

    async with Client(mcp_server) as client:
        # Create multiple notes
        note_titles = [
            "First Note",
            "Second Note",
            "Third Note",
            "Fourth Note",
            "Fifth Note",
        ]

        for title in note_titles:
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": title,
                    "folder": "batch",
                    "content": f"# {title}\n\nContent for {title}",
                    "tags": "batch,test",
                },
            )

        # Delete all notes sequentially
        for title in note_titles:
            delete_result = await client.call_tool(
                "delete_note",
                {
                    "project": test_project.name,
                    "identifier": title,
                },
            )

            # Each deletion should be successful
            assert "true" in delete_result.content[0].text.lower(), f"Failed to delete {title}"

        # Verify all notes are deleted by searching
        search_result = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "batch",
            },
        )

        # Should have no results
        assert (
            '"results": []' in search_result.content[0].text
            or '"results":[]' in search_result.content[0].text
        )


@pytest.mark.asyncio
async def test_delete_note_with_unicode_content(mcp_server, app, test_project):
    """Test deleting notes with Unicode content."""

    async with Client(mcp_server) as client:
        # Create a note with Unicode content
        unicode_content = """# Unicode Test Note 🚀

This note contains various Unicode characters:
- Emojis: 🎉 🔥 ⚡ 💡
- Languages: 测试中文 Tëst Übër
- Symbols: ♠♣♥♦ ←→↑↓ ∞≠≤≥
- Math: ∑∏∂∇∆Ω

## Observations
- [test] Unicode characters preserved ✓
- [note] Emoji support working 🎯

## Relations  
- supports [[Unicode Standards]]
- tested_with [[Various Languages]]"""

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Unicode Test Note",
                "folder": "unicode",
                "content": unicode_content,
                "tags": "unicode,test,emoji",
            },
        )

        # Delete the Unicode note
        delete_result = await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": "Unicode Test Note",
            },
        )

        # Should return True for successful deletion
        assert "true" in delete_result.content[0].text.lower()

        # Verify deletion
        read_after_delete = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "Unicode Test Note",
            },
        )

        # Should return "Note Not Found" message
        assert len(read_after_delete.content) == 1
        result_text = read_after_delete.content[0].text
        assert "Note Not Found" in result_text
        assert "Unicode Test Note" in result_text
