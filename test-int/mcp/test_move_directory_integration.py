"""
Integration tests for move_note with is_directory=True.

Tests the complete directory move workflow: MCP client -> MCP server -> FastAPI -> database -> file system
"""

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_move_directory_basic(mcp_server, app, test_project):
    """Test basic directory move operation."""

    async with Client(mcp_server) as client:
        # Create multiple notes in a source directory
        for i in range(3):
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": f"Doc {i + 1}",
                    "directory": "source-dir",
                    "content": f"# Doc {i + 1}\n\nContent for document {i + 1}.",
                    "tags": "test,move-dir",
                },
            )

        # Move the entire directory
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "source-dir",
                "destination_path": "dest-dir",
                "is_directory": True,
            },
        )

        # Should return successful move message with summary
        assert len(move_result.content) == 1
        move_text = move_result.content[0].text
        assert "Directory Moved Successfully" in move_text
        assert "Total files: 3" in move_text
        assert "source-dir" in move_text
        assert "dest-dir" in move_text

        # Verify all notes can be read from new locations
        for i in range(3):
            read_result = await client.call_tool(
                "read_note",
                {
                    "project": test_project.name,
                    "identifier": f"dest-dir/doc-{i + 1}",
                },
            )
            content = read_result.content[0].text
            assert f"Content for document {i + 1}" in content

        # Verify original locations no longer exist
        for i in range(3):
            read_original = await client.call_tool(
                "read_note",
                {
                    "project": test_project.name,
                    "identifier": f"source-dir/doc-{i + 1}",
                },
            )
            assert "Note Not Found" in read_original.content[0].text


@pytest.mark.asyncio
async def test_move_directory_nested(mcp_server, app, test_project):
    """Test moving a directory with nested subdirectories."""

    async with Client(mcp_server) as client:
        # Create notes in nested structure
        directories = [
            "projects/2024",
            "projects/2024/q1",
            "projects/2024/q2",
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

        # Move the parent directory
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "projects/2024",
                "destination_path": "archive/2024",
                "is_directory": True,
            },
        )

        # Should move all nested files
        assert len(move_result.content) == 1
        move_text = move_result.content[0].text
        assert "Directory Moved Successfully" in move_text
        assert "Total files: 3" in move_text

        # Verify nested structure is preserved
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "archive/2024/q1/note-in-q1",
            },
        )
        assert "Content in projects/2024/q1" in read_result.content[0].text

        read_result2 = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "archive/2024/q2/note-in-q2",
            },
        )
        assert "Content in projects/2024/q2" in read_result2.content[0].text


@pytest.mark.asyncio
async def test_move_directory_empty(mcp_server, app, test_project):
    """Test moving an empty directory returns appropriate message."""

    async with Client(mcp_server) as client:
        # Try to move a non-existent/empty directory
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "nonexistent-dir",
                "destination_path": "dest-dir",
                "is_directory": True,
            },
        )

        # Should return message about no files found
        assert len(move_result.content) == 1
        move_text = move_result.content[0].text
        assert "No files found" in move_text or "0" in move_text


@pytest.mark.asyncio
async def test_move_directory_preserves_content(mcp_server, app, test_project):
    """Test that directory move preserves all note content including observations and relations."""

    async with Client(mcp_server) as client:
        # Create note with complex content
        complex_content = """# Complex Note

## Observations
- [feature] Important feature observation
- [tech] Technical detail here

## Relations
- relates_to [[Other Note]]
- implements [[Specification]]

## Content
Detailed content that must be preserved."""

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Complex Note",
                "directory": "source-complex",
                "content": complex_content,
                "tags": "test,complex",
            },
        )

        # Move the directory
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "source-complex",
                "destination_path": "dest-complex",
                "is_directory": True,
            },
        )

        assert "Directory Moved Successfully" in move_result.content[0].text

        # Verify content preservation
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "dest-complex/complex-note",
            },
        )

        content = read_result.content[0].text
        assert "Important feature observation" in content
        assert "Technical detail here" in content
        assert "relates_to [[Other Note]]" in content
        assert "Detailed content that must be preserved" in content


@pytest.mark.asyncio
async def test_move_directory_search_still_works(mcp_server, app, test_project):
    """Test that moved directory contents remain searchable."""

    async with Client(mcp_server) as client:
        # Create searchable note
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Searchable Doc",
                "directory": "searchable-dir",
                "content": "# Searchable Doc\n\nUnique quantum entanglement research content.",
                "tags": "search,test",
            },
        )

        # Verify searchable before move
        search_before = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "quantum entanglement",
            },
        )
        assert "Searchable Doc" in search_before.content[0].text

        # Move directory
        await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "searchable-dir",
                "destination_path": "moved-searchable",
                "is_directory": True,
            },
        )

        # Verify still searchable after move
        search_after = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "quantum entanglement",
            },
        )
        search_text = search_after.content[0].text
        assert "quantum entanglement" in search_text
        assert "moved-searchable" in search_text or "searchable-doc" in search_text


@pytest.mark.asyncio
async def test_move_directory_single_file(mcp_server, app, test_project):
    """Test moving a directory with only one file."""

    async with Client(mcp_server) as client:
        # Create single note in directory
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Single Note",
                "directory": "single-dir",
                "content": "# Single Note\n\nOnly note in this directory.",
                "tags": "test,single",
            },
        )

        # Move directory
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "single-dir",
                "destination_path": "moved-single",
                "is_directory": True,
            },
        )

        assert len(move_result.content) == 1
        move_text = move_result.content[0].text
        assert "Directory Moved Successfully" in move_text
        assert "Total files: 1" in move_text

        # Verify note at new location
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "moved-single/single-note",
            },
        )
        assert "Only note in this directory" in read_result.content[0].text


@pytest.mark.asyncio
async def test_move_directory_many_files(mcp_server, app, test_project):
    """Test moving a directory with more than 10 files shows truncated list."""

    async with Client(mcp_server) as client:
        # Create 12 notes in the directory
        for i in range(12):
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": f"ManyDoc {i + 1}",
                    "directory": "many-files-dir",
                    "content": f"# Many Doc {i + 1}\n\nContent {i + 1}.",
                    "tags": "test,many",
                },
            )

        # Move the directory
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "many-files-dir",
                "destination_path": "moved-many",
                "is_directory": True,
            },
        )

        assert len(move_result.content) == 1
        move_text = move_result.content[0].text
        assert "Directory Moved Successfully" in move_text
        assert "Total files: 12" in move_text
        # Should show truncation message for >10 files
        assert "... and 2 more" in move_text
