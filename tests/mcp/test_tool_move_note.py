"""Tests for the move_note MCP tool."""

import pytest
from unittest.mock import patch

from basic_memory.mcp.tools.move_note import move_note, _format_move_error_response
from basic_memory.mcp.tools.write_note import write_note
from basic_memory.mcp.tools.read_note import read_note


@pytest.mark.asyncio
async def test_move_note_success(app, client):
    """Test successfully moving a note to a new location."""
    # Create initial note
    await write_note.fn(
        title="Test Note",
        folder="source",
        content="# Test Note\nOriginal content here.",
    )

    # Move note
    result = await move_note.fn(
        identifier="source/test-note",
        destination_path="target/MovedNote.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify original location no longer exists
    try:
        await read_note.fn("source/test-note")
        assert False, "Original note should not exist after move"
    except Exception:
        pass  # Expected - note should not exist at original location

    # Verify note exists at new location with same content
    content = await read_note.fn("target/moved-note")
    assert "# Test Note" in content
    assert "Original content here" in content
    assert "permalink: target/moved-note" in content


@pytest.mark.asyncio
async def test_move_note_with_folder_creation(client):
    """Test moving note creates necessary folders."""
    # Create initial note
    await write_note.fn(
        title="Deep Note",
        folder="",
        content="# Deep Note\nContent in root folder.",
    )

    # Move to deeply nested path
    result = await move_note.fn(
        identifier="deep-note",
        destination_path="deeply/nested/folder/DeepNote.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify note exists at new location
    content = await read_note.fn("deeply/nested/folder/deep-note")
    assert "# Deep Note" in content
    assert "Content in root folder" in content


@pytest.mark.asyncio
async def test_move_note_with_observations_and_relations(app, client):
    """Test moving note preserves observations and relations."""
    # Create note with complex semantic content
    await write_note.fn(
        title="Complex Entity",
        folder="source",
        content="""# Complex Entity

## Observations
- [note] Important observation #tag1
- [feature] Key feature #feature

## Relations
- relation to [[SomeOtherEntity]]
- depends on [[Dependency]]

Some additional content.
        """,
    )

    # Move note
    result = await move_note.fn(
        identifier="source/complex-entity",
        destination_path="target/MovedComplex.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify moved note preserves all content
    content = await read_note.fn("target/moved-complex")
    assert "Important observation #tag1" in content
    assert "Key feature #feature" in content
    assert "[[SomeOtherEntity]]" in content
    assert "[[Dependency]]" in content
    assert "Some additional content" in content


@pytest.mark.asyncio
async def test_move_note_by_title(client):
    """Test moving note using title as identifier."""
    # Create note with unique title
    await write_note.fn(
        title="UniqueTestTitle",
        folder="source",
        content="# UniqueTestTitle\nTest content.",
    )

    # Move using title as identifier
    result = await move_note.fn(
        identifier="UniqueTestTitle",
        destination_path="target/MovedByTitle.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify note exists at new location
    content = await read_note.fn("target/moved-by-title")
    assert "# UniqueTestTitle" in content
    assert "Test content" in content


@pytest.mark.asyncio
async def test_move_note_by_file_path(client):
    """Test moving note using file path as identifier."""
    # Create initial note
    await write_note.fn(
        title="PathTest",
        folder="source",
        content="# PathTest\nContent for path test.",
    )

    # Move using file path as identifier
    result = await move_note.fn(
        identifier="source/PathTest.md",
        destination_path="target/MovedByPath.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify note exists at new location
    content = await read_note.fn("target/moved-by-path")
    assert "# PathTest" in content
    assert "Content for path test" in content


@pytest.mark.asyncio
async def test_move_note_nonexistent_note(client):
    """Test moving a note that doesn't exist."""
    result = await move_note.fn(
        identifier="nonexistent/note",
        destination_path="target/SomeFile.md",
    )

    # Should return user-friendly error message string
    assert isinstance(result, str)
    assert "# Move Failed - Note Not Found" in result
    assert "could not be found for moving" in result
    assert "Search for the note first" in result


@pytest.mark.asyncio
async def test_move_note_invalid_destination_path(client):
    """Test moving note with invalid destination path."""
    # Create initial note
    await write_note.fn(
        title="TestNote",
        folder="source",
        content="# TestNote\nTest content.",
    )

    # Test absolute path (should be rejected by validation)
    result = await move_note.fn(
        identifier="source/test-note",
        destination_path="/absolute/path.md",
    )

    # Should return user-friendly error message string
    assert isinstance(result, str)
    assert "# Move Failed" in result
    assert "/absolute/path.md" in result or "Invalid" in result or "path" in result


@pytest.mark.asyncio
async def test_move_note_destination_exists(client):
    """Test moving note to existing destination."""
    # Create source note
    await write_note.fn(
        title="SourceNote",
        folder="source",
        content="# SourceNote\nSource content.",
    )

    # Create destination note
    await write_note.fn(
        title="DestinationNote",
        folder="target",
        content="# DestinationNote\nDestination content.",
    )

    # Try to move source to existing destination
    result = await move_note.fn(
        identifier="source/source-note",
        destination_path="target/DestinationNote.md",
    )

    # Should return user-friendly error message string
    assert isinstance(result, str)
    assert "# Move Failed" in result
    assert "already exists" in result or "Destination" in result


@pytest.mark.asyncio
async def test_move_note_same_location(client):
    """Test moving note to the same location."""
    # Create initial note
    await write_note.fn(
        title="SameLocationTest",
        folder="test",
        content="# SameLocationTest\nContent here.",
    )

    # Try to move to same location
    result = await move_note.fn(
        identifier="test/same-location-test",
        destination_path="test/SameLocationTest.md",
    )

    # Should return user-friendly error message string
    assert isinstance(result, str)
    assert "# Move Failed" in result
    assert "already exists" in result or "same" in result or "Destination" in result


@pytest.mark.asyncio
async def test_move_note_rename_only(client):
    """Test moving note within same folder (rename operation)."""
    # Create initial note
    await write_note.fn(
        title="OriginalName",
        folder="test",
        content="# OriginalName\nContent to rename.",
    )

    # Rename within same folder
    await move_note.fn(
        identifier="test/original-name",
        destination_path="test/NewName.md",
    )

    # Verify original is gone
    try:
        await read_note.fn("test/original-name")
        assert False, "Original note should not exist after rename"
    except Exception:
        pass  # Expected

    # Verify new name exists with same content
    content = await read_note.fn("test/new-name")
    assert "# OriginalName" in content  # Title in content remains same
    assert "Content to rename" in content
    assert "permalink: test/new-name" in content


@pytest.mark.asyncio
async def test_move_note_complex_filename(client):
    """Test moving note with spaces in filename."""
    # Create note with spaces in name
    await write_note.fn(
        title="Meeting Notes 2025",
        folder="meetings",
        content="# Meeting Notes 2025\nMeeting content with dates.",
    )

    # Move to new location
    result = await move_note.fn(
        identifier="meetings/meeting-notes-2025",
        destination_path="archive/2025/meetings/Meeting Notes 2025.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify note exists at new location with correct content
    content = await read_note.fn("archive/2025/meetings/meeting-notes-2025")
    assert "# Meeting Notes 2025" in content
    assert "Meeting content with dates" in content


@pytest.mark.asyncio
async def test_move_note_with_tags(app, client):
    """Test moving note with tags preserves tags."""
    # Create note with tags
    await write_note.fn(
        title="Tagged Note",
        folder="source",
        content="# Tagged Note\nContent with tags.",
        tags=["important", "work", "project"],
    )

    # Move note
    result = await move_note.fn(
        identifier="source/tagged-note",
        destination_path="target/MovedTaggedNote.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify tags are preserved in correct YAML format
    content = await read_note.fn("target/moved-tagged-note")
    assert "- important" in content
    assert "- work" in content
    assert "- project" in content


@pytest.mark.asyncio
async def test_move_note_empty_string_destination(client):
    """Test moving note with empty destination path."""
    # Create initial note
    await write_note.fn(
        title="TestNote",
        folder="source",
        content="# TestNote\nTest content.",
    )

    # Test empty destination path
    result = await move_note.fn(
        identifier="source/test-note",
        destination_path="",
    )

    # Should return user-friendly error message string
    assert isinstance(result, str)
    assert "# Move Failed" in result
    assert "empty" in result or "Invalid" in result or "path" in result


@pytest.mark.asyncio
async def test_move_note_parent_directory_path(client):
    """Test moving note with parent directory in destination path."""
    # Create initial note
    await write_note.fn(
        title="TestNote",
        folder="source",
        content="# TestNote\nTest content.",
    )

    # Test parent directory path
    result = await move_note.fn(
        identifier="source/test-note",
        destination_path="../parent/file.md",
    )

    # Should return user-friendly error message string
    assert isinstance(result, str)
    assert "# Move Failed" in result
    assert "parent" in result or "Invalid" in result or "path" in result or ".." in result


@pytest.mark.asyncio
async def test_move_note_identifier_variations(client):
    """Test that various identifier formats work for moving."""
    # Create a note to test different identifier formats
    await write_note.fn(
        title="Test Document",
        folder="docs",
        content="# Test Document\nContent for testing identifiers.",
    )

    # Test with permalink identifier
    result = await move_note.fn(
        identifier="docs/test-document",
        destination_path="moved/TestDocument.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify it moved correctly
    content = await read_note.fn("moved/test-document")
    assert "# Test Document" in content
    assert "Content for testing identifiers" in content


@pytest.mark.asyncio
async def test_move_note_preserves_frontmatter(app, client):
    """Test that moving preserves custom frontmatter."""
    # Create note with custom frontmatter by first creating it normally
    await write_note.fn(
        title="Custom Frontmatter Note",
        folder="source",
        content="# Custom Frontmatter Note\nContent with custom metadata.",
    )

    # Move the note
    result = await move_note.fn(
        identifier="source/custom-frontmatter-note",
        destination_path="target/MovedCustomNote.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify the moved note has proper frontmatter structure
    content = await read_note.fn("target/moved-custom-note")
    assert "title: Custom Frontmatter Note" in content
    assert "type: note" in content
    assert "permalink: target/moved-custom-note" in content
    assert "# Custom Frontmatter Note" in content
    assert "Content with custom metadata" in content


class TestMoveNoteErrorFormatting:
    """Test move note error formatting for better user experience."""

    def test_format_move_error_invalid_path(self):
        """Test formatting for invalid path errors."""
        result = _format_move_error_response("invalid path format", "test-note", "/invalid/path.md")

        assert "# Move Failed - Invalid Destination Path" in result
        assert "The destination path '/invalid/path.md' is not valid" in result
        assert "Relative paths only" in result
        assert "Include file extension" in result

    def test_format_move_error_permission_denied(self):
        """Test formatting for permission errors."""
        result = _format_move_error_response("permission denied", "test-note", "target/file.md")

        assert "# Move Failed - Permission Error" in result
        assert "You don't have permission to move 'test-note'" in result
        assert "Check file permissions" in result
        assert "Check file locks" in result

    def test_format_move_error_source_missing(self):
        """Test formatting for source file missing errors."""
        result = _format_move_error_response("source file missing", "test-note", "target/file.md")

        assert "# Move Failed - Source File Missing" in result
        assert "The source file for 'test-note' was not found on disk" in result
        assert "database and filesystem are out of sync" in result

    def test_format_move_error_server_error(self):
        """Test formatting for server errors."""
        result = _format_move_error_response("server error occurred", "test-note", "target/file.md")

        assert "# Move Failed - System Error" in result
        assert "A system error occurred while moving 'test-note'" in result
        assert "Try again" in result
        assert "Check disk space" in result


class TestMoveNoteErrorHandling:
    """Test move note exception handling."""

    @pytest.mark.asyncio
    async def test_move_note_exception_handling(self):
        """Test exception handling in move_note."""
        with patch("basic_memory.mcp.tools.move_note.get_active_project") as mock_get_project:
            mock_get_project.return_value.project_url = "http://test"
            mock_get_project.return_value.name = "test-project"

            with patch(
                "basic_memory.mcp.tools.move_note.call_post",
                side_effect=Exception("entity not found"),
            ):
                result = await move_note.fn("test-note", "target/file.md")

                assert isinstance(result, str)
                assert "# Move Failed - Note Not Found" in result

    @pytest.mark.asyncio
    async def test_move_note_permission_error_handling(self):
        """Test permission error handling in move_note."""
        with patch("basic_memory.mcp.tools.move_note.get_active_project") as mock_get_project:
            mock_get_project.return_value.project_url = "http://test"
            mock_get_project.return_value.name = "test-project"

            with patch(
                "basic_memory.mcp.tools.move_note.call_post",
                side_effect=Exception("permission denied"),
            ):
                result = await move_note.fn("test-note", "target/file.md")

                assert isinstance(result, str)
                assert "# Move Failed - Permission Error" in result
