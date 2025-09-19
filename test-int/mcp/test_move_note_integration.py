"""
Integration tests for move_note MCP tool.

Tests the complete move note workflow: MCP client -> MCP server -> FastAPI -> database -> file system
"""

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_move_note_basic_operation(mcp_server, app, test_project):
    """Test basic move note operation to a new folder."""

    async with Client(mcp_server) as client:
        # Create a note to move
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Move Test Note",
                "folder": "source",
                "content": "# Move Test Note\n\nThis note will be moved to a new location.",
                "tags": "test,move",
            },
        )

        # Move the note to a new location
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Move Test Note",
                "destination_path": "destination/moved-note.md",
            },
        )

        # Should return successful move message
        assert len(move_result.content) == 1
        move_text = move_result.content[0].text
        assert "✅ Note moved successfully" in move_text
        assert "Move Test Note" in move_text
        assert "destination/moved-note.md" in move_text
        assert "📊 Database and search index updated" in move_text

        # Verify the note can be read from its new location
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "destination/moved-note.md",
            },
        )

        content = read_result.content[0].text
        assert "This note will be moved to a new location" in content

        # Verify the original location no longer works
        read_original = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "source/move-test-note.md",
            },
        )

        # Should return "Note Not Found" message
        assert "Note Not Found" in read_original.content[0].text


@pytest.mark.asyncio
async def test_move_note_using_permalink(mcp_server, app, test_project):
    """Test moving a note using its permalink as identifier."""

    async with Client(mcp_server) as client:
        # Create a note to move
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Permalink Move Test",
                "folder": "test",
                "content": "# Permalink Move Test\n\nMoving by permalink.",
                "tags": "test,permalink",
            },
        )

        # Move using permalink
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "test/permalink-move-test",
                "destination_path": "archive/permalink-moved.md",
            },
        )

        # Should successfully move
        assert len(move_result.content) == 1
        move_text = move_result.content[0].text
        assert "✅ Note moved successfully" in move_text
        assert "test/permalink-move-test" in move_text
        assert "archive/permalink-moved.md" in move_text

        # Verify accessibility at new location
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "archive/permalink-moved.md",
            },
        )

        assert "Moving by permalink" in read_result.content[0].text


@pytest.mark.asyncio
async def test_move_note_with_observations_and_relations(mcp_server, app, test_project):
    """Test moving a note that contains observations and relations."""

    async with Client(mcp_server) as client:
        # Create complex note with observations and relations
        complex_content = """# Complex Note

This note has various structured content.

## Observations
- [feature] Has structured observations
- [tech] Uses markdown format
- [status] Ready for move testing

## Relations
- implements [[Auth System]]
- documented_in [[Move Guide]]
- depends_on [[File System]]

## Content
This note demonstrates moving complex content."""

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Complex Note",
                "folder": "complex",
                "content": complex_content,
                "tags": "test,complex,move",
            },
        )

        # Move the complex note
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Complex Note",
                "destination_path": "moved/complex-note.md",
            },
        )

        # Should successfully move
        assert len(move_result.content) == 1
        move_text = move_result.content[0].text
        assert "✅ Note moved successfully" in move_text
        assert "Complex Note" in move_text
        assert "moved/complex-note.md" in move_text

        # Verify content preservation including structured data
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "moved/complex-note.md",
            },
        )

        content = read_result.content[0].text
        assert "Has structured observations" in content
        assert "implements [[Auth System]]" in content
        assert "## Observations" in content
        assert "[feature]" in content  # Should show original markdown observations
        assert "## Relations" in content


@pytest.mark.asyncio
async def test_move_note_to_nested_directory(mcp_server, app, test_project):
    """Test moving a note to a deeply nested directory structure."""

    async with Client(mcp_server) as client:
        # Create a note
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Nested Move Test",
                "folder": "root",
                "content": "# Nested Move Test\n\nThis will be moved deep.",
                "tags": "test,nested",
            },
        )

        # Move to a deep nested structure
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Nested Move Test",
                "destination_path": "projects/2025/q2/work/nested-note.md",
            },
        )

        # Should successfully create directory structure and move
        assert len(move_result.content) == 1
        move_text = move_result.content[0].text
        assert "✅ Note moved successfully" in move_text
        assert "Nested Move Test" in move_text
        assert "projects/2025/q2/work/nested-note.md" in move_text

        # Verify accessibility
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "projects/2025/q2/work/nested-note.md",
            },
        )

        assert "This will be moved deep" in read_result.content[0].text


@pytest.mark.asyncio
async def test_move_note_with_special_characters(mcp_server, app, test_project):
    """Test moving notes with special characters in titles and paths."""

    async with Client(mcp_server) as client:
        # Create note with special characters
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Special (Chars) & Symbols",
                "folder": "special",
                "content": "# Special (Chars) & Symbols\n\nTesting special characters in move.",
                "tags": "test,special",
            },
        )

        # Move to path with special characters
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Special (Chars) & Symbols",
                "destination_path": "archive/special-chars-note.md",
            },
        )

        # Should handle special characters properly
        assert len(move_result.content) == 1
        move_text = move_result.content[0].text
        assert "✅ Note moved successfully" in move_text
        assert "archive/special-chars-note.md" in move_text

        # Verify content preservation
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "archive/special-chars-note.md",
            },
        )

        assert "Testing special characters in move" in read_result.content[0].text


@pytest.mark.asyncio
async def test_move_note_error_handling_note_not_found(mcp_server, app, test_project):
    """Test error handling when trying to move a non-existent note."""

    async with Client(mcp_server) as client:
        # Try to move a note that doesn't exist - should return error message
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Non-existent Note",
                "destination_path": "new/location.md",
            },
        )

        # Should contain error message about the failed operation
        assert len(move_result.content) == 1
        error_message = move_result.content[0].text
        assert "# Move Failed" in error_message
        assert "Non-existent Note" in error_message


@pytest.mark.asyncio
async def test_move_note_error_handling_invalid_destination(mcp_server, app, test_project):
    """Test error handling for invalid destination paths."""

    async with Client(mcp_server) as client:
        # Create a note to attempt moving
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Invalid Dest Test",
                "folder": "test",
                "content": "# Invalid Dest Test\n\nThis move should fail.",
                "tags": "test,error",
            },
        )

        # Try to move to absolute path (should fail) - should return error message
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Invalid Dest Test",
                "destination_path": "/absolute/path/note.md",
            },
        )

        # Should contain error message about the failed operation
        assert len(move_result.content) == 1
        error_message = move_result.content[0].text
        assert "# Move Failed" in error_message
        assert "/absolute/path/note.md" in error_message


@pytest.mark.asyncio
async def test_move_note_error_handling_destination_exists(mcp_server, app, test_project):
    """Test error handling when destination file already exists."""

    async with Client(mcp_server) as client:
        # Create source note
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Source Note",
                "folder": "source",
                "content": "# Source Note\n\nThis is the source.",
                "tags": "test,source",
            },
        )

        # Create destination note that already exists at the exact path we'll try to move to
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Existing Note",
                "folder": "destination",
                "content": "# Existing Note\n\nThis already exists.",
                "tags": "test,existing",
            },
        )

        # Try to move source to existing destination (should fail) - should return error message
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Source Note",
                "destination_path": "destination/Existing Note.md",  # Use exact existing file name
            },
        )

        # Should contain error message about the failed operation
        assert len(move_result.content) == 1
        error_message = move_result.content[0].text
        assert "# Move Failed" in error_message
        assert "already exists" in error_message


@pytest.mark.asyncio
async def test_move_note_preserves_search_functionality(mcp_server, app, test_project):
    """Test that moved notes remain searchable after move operation."""

    async with Client(mcp_server) as client:
        # Create a note with searchable content
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Searchable Note",
                "folder": "original",
                "content": """# Searchable Note

This note contains unique search terms:
- quantum mechanics
- artificial intelligence
- machine learning algorithms

## Features
- [technology] Advanced AI features
- [research] Quantum computing research

## Relations
- relates_to [[AI Research]]""",
                "tags": "search,test,move",
            },
        )

        # Verify note is searchable before move
        search_before = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "quantum mechanics",
            },
        )

        assert len(search_before.content) > 0
        assert "Searchable Note" in search_before.content[0].text

        # Move the note
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Searchable Note",
                "destination_path": "research/quantum-ai-note.md",
            },
        )

        assert len(move_result.content) == 1
        move_text = move_result.content[0].text
        assert "✅ Note moved successfully" in move_text

        # Verify note is still searchable after move
        search_after = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "quantum mechanics",
            },
        )

        assert len(search_after.content) > 0
        search_text = search_after.content[0].text
        assert "quantum mechanics" in search_text
        assert "research/quantum-ai-note.md" in search_text or "quantum-ai-note" in search_text

        # Verify search by new location works
        search_by_path = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "research/quantum",
            },
        )

        assert len(search_by_path.content) > 0


@pytest.mark.asyncio
async def test_move_note_using_different_identifier_formats(mcp_server, app, test_project):
    """Test moving notes using different identifier formats (title, permalink, folder/title)."""

    async with Client(mcp_server) as client:
        # Create notes for different identifier tests
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Title ID Note",
                "folder": "test",
                "content": "# Title ID Note\n\nMove by title.",
                "tags": "test,identifier",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Permalink ID Note",
                "folder": "test",
                "content": "# Permalink ID Note\n\nMove by permalink.",
                "tags": "test,identifier",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Folder Title Note",
                "folder": "test",
                "content": "# Folder Title Note\n\nMove by folder/title.",
                "tags": "test,identifier",
            },
        )

        # Test moving by title
        move1 = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Title ID Note",  # by title
                "destination_path": "moved/title-moved.md",
            },
        )
        assert len(move1.content) == 1
        assert "✅ Note moved successfully" in move1.content[0].text

        # Test moving by permalink
        move2 = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "test/permalink-id-note",  # by permalink
                "destination_path": "moved/permalink-moved.md",
            },
        )
        assert len(move2.content) == 1
        assert "✅ Note moved successfully" in move2.content[0].text

        # Test moving by folder/title format
        move3 = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "test/Folder Title Note",  # by folder/title
                "destination_path": "moved/folder-title-moved.md",
            },
        )
        assert len(move3.content) == 1
        assert "✅ Note moved successfully" in move3.content[0].text

        # Verify all notes can be accessed at their new locations
        read1 = await client.call_tool(
            "read_note", {"project": test_project.name, "identifier": "moved/title-moved.md"}
        )
        assert "Move by title" in read1.content[0].text

        read2 = await client.call_tool(
            "read_note", {"project": test_project.name, "identifier": "moved/permalink-moved.md"}
        )
        assert "Move by permalink" in read2.content[0].text

        read3 = await client.call_tool(
            "read_note", {"project": test_project.name, "identifier": "moved/folder-title-moved.md"}
        )
        assert "Move by folder/title" in read3.content[0].text


@pytest.mark.asyncio
async def test_move_note_cross_project_detection(mcp_server, app, test_project):
    """Test cross-project move detection and helpful error messages."""

    async with Client(mcp_server) as client:
        # Create a test project to simulate cross-project scenario
        await client.call_tool(
            "create_memory_project",
            {
                "project_name": "test-project-b",
                "project_path": "/tmp/test-project-b",
                "set_default": False,
            },
        )

        # Create a note in the default project
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Cross Project Test Note",
                "folder": "source",
                "content": "# Cross Project Test Note\n\nThis note is in the default project.",
                "tags": "test,cross-project",
            },
        )

        # Try to move to a path that contains the other project name
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Cross Project Test Note",
                "destination_path": "test-project-b/moved-note.md",
            },
        )

        # Should detect cross-project attempt and provide helpful guidance
        assert len(move_result.content) == 1
        error_message = move_result.content[0].text
        assert "Cross-Project Move Not Supported" in error_message
        assert "test-project-b" in error_message
        assert "read_note" in error_message
        assert "write_note" in error_message


@pytest.mark.asyncio
async def test_move_note_normal_moves_still_work(mcp_server, app, test_project):
    """Test that normal within-project moves still work after cross-project detection."""

    async with Client(mcp_server) as client:
        # Create a note
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Normal Move Note",
                "folder": "source",
                "content": "# Normal Move Note\n\nThis should move normally.",
                "tags": "test,normal-move",
            },
        )

        # Try a normal move that should work
        move_result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "Normal Move Note",
                "destination_path": "destination/normal-moved.md",
            },
        )

        # Should work normally
        assert len(move_result.content) == 1
        move_text = move_result.content[0].text
        assert "✅ Note moved successfully" in move_text
        assert "Normal Move Note" in move_text
        assert "destination/normal-moved.md" in move_text

        # Verify the note can be read from its new location
        read_result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "destination/normal-moved.md",
            },
        )

        content = read_result.content[0].text
        assert "This should move normally" in content
