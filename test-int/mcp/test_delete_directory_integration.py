"""
Integration tests for delete_note with is_directory=True.

Tests the complete directory delete workflow: MCP client -> MCP server -> FastAPI -> database -> file system
"""

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_delete_directory_basic(mcp_server, app, test_project):
    """Test basic directory delete operation."""

    async with Client(mcp_server) as client:
        # Create multiple notes in a source directory
        for i in range(3):
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": f"Doc {i + 1}",
                    "directory": "delete-dir",
                    "content": f"# Doc {i + 1}\n\nContent for document {i + 1}.",
                    "tags": "test,delete-dir",
                },
            )

        # Verify notes exist
        for i in range(3):
            read_result = await client.call_tool(
                "read_note",
                {
                    "project": test_project.name,
                    "identifier": f"delete-dir/doc-{i + 1}",
                },
            )
            assert f"Content for document {i + 1}" in read_result.content[0].text

        # Delete the entire directory
        delete_result = await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": "delete-dir",
                "is_directory": True,
            },
        )

        # Should return successful delete message with summary
        assert len(delete_result.content) == 1
        delete_text = delete_result.content[0].text
        assert "Directory Deleted Successfully" in delete_text
        assert "Total files: 3" in delete_text
        assert "delete-dir" in delete_text

        # Verify all notes are deleted
        for i in range(3):
            read_result = await client.call_tool(
                "read_note",
                {
                    "project": test_project.name,
                    "identifier": f"delete-dir/doc-{i + 1}",
                },
            )
            assert "Note Not Found" in read_result.content[0].text


@pytest.mark.asyncio
async def test_delete_directory_nested(mcp_server, app, test_project):
    """Test deleting a directory with nested subdirectories."""

    async with Client(mcp_server) as client:
        # Create notes in nested structure
        directories = [
            "to-delete/2024",
            "to-delete/2024/q1",
            "to-delete/2024/q2",
        ]

        for dir_path in directories:
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": f"Note in {dir_path.split('/')[-1]}",
                    "directory": dir_path,
                    "content": f"# Note\n\nContent in {dir_path}.",
                    "tags": "test,nested",
                },
            )

        # Delete the parent directory
        delete_result = await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": "to-delete/2024",
                "is_directory": True,
            },
        )

        # Should delete all nested files
        assert len(delete_result.content) == 1
        delete_text = delete_result.content[0].text
        assert "Directory Deleted Successfully" in delete_text
        assert "Total files: 3" in delete_text

        # Verify all nested notes are deleted
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "to-delete/2024/q1/note-in-q1",
            },
        )
        assert "Note Not Found" in read_result.content[0].text


@pytest.mark.asyncio
async def test_delete_directory_empty(mcp_server, app, test_project):
    """Test deleting an empty/non-existent directory returns appropriate message."""

    async with Client(mcp_server) as client:
        # Try to delete a non-existent/empty directory
        delete_result = await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": "nonexistent-dir",
                "is_directory": True,
            },
        )

        # Should return message about no files found
        assert len(delete_result.content) == 1
        delete_text = delete_result.content[0].text
        # Either shows "Directory Deleted Successfully" with 0 files or similar
        assert "Total files: 0" in delete_text or "0" in delete_text


@pytest.mark.asyncio
async def test_delete_directory_single_file(mcp_server, app, test_project):
    """Test deleting a directory with only one file."""

    async with Client(mcp_server) as client:
        # Create single note in directory
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Single Note",
                "directory": "single-delete-dir",
                "content": "# Single Note\n\nOnly note in this directory.",
                "tags": "test,single",
            },
        )

        # Delete directory
        delete_result = await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": "single-delete-dir",
                "is_directory": True,
            },
        )

        assert len(delete_result.content) == 1
        delete_text = delete_result.content[0].text
        assert "Directory Deleted Successfully" in delete_text
        assert "Total files: 1" in delete_text

        # Verify note is deleted
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "single-delete-dir/single-note",
            },
        )
        assert "Note Not Found" in read_result.content[0].text


@pytest.mark.asyncio
async def test_delete_directory_search_no_longer_finds(mcp_server, app, test_project):
    """Test that deleted directory contents are no longer searchable."""

    async with Client(mcp_server) as client:
        # Create searchable note
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Searchable Delete Doc",
                "directory": "searchable-delete-dir",
                "content": "# Searchable Delete Doc\n\nUnique fusionreactor quantum content.",
                "tags": "search,test",
            },
        )

        # Verify searchable before delete
        search_before = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "fusionreactor quantum",
            },
        )
        assert "Searchable Delete Doc" in search_before.content[0].text

        # Delete directory
        await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": "searchable-delete-dir",
                "is_directory": True,
            },
        )

        # Verify no longer searchable after delete
        search_after = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "fusionreactor quantum",
            },
        )
        search_text = search_after.content[0].text
        # Should not find the deleted note
        assert "Searchable Delete Doc" not in search_text or "No results" in search_text


@pytest.mark.asyncio
async def test_delete_directory_many_files(mcp_server, app, test_project):
    """Test deleting a directory with more than 10 files shows truncated list."""

    async with Client(mcp_server) as client:
        # Create 12 notes in the directory
        for i in range(12):
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": f"DeleteManyDoc {i + 1}",
                    "directory": "delete-many-dir",
                    "content": f"# Delete Many Doc {i + 1}\n\nContent {i + 1}.",
                    "tags": "test,many",
                },
            )

        # Delete the entire directory
        delete_result = await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": "delete-many-dir",
                "is_directory": True,
            },
        )

        assert len(delete_result.content) == 1
        delete_text = delete_result.content[0].text
        assert "Directory Deleted Successfully" in delete_text
        assert "Total files: 12" in delete_text
        # Should show truncation message for >10 files
        assert "... and 2 more" in delete_text
