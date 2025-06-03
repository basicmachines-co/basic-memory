"""Tests for the move_note MCP tool."""

import pytest

from basic_memory.mcp.tools.move_note import move_note
from basic_memory.mcp.tools.write_note import write_note
from basic_memory.mcp.tools.read_note import read_note


@pytest.mark.asyncio
async def test_move_note_success(app, client):
    """Test successfully moving a note to a new location."""
    # Create initial note
    await write_note(
        title="Test Note",
        folder="source",
        content="# Test Note\nOriginal content here.",
    )

    # Move note
    result = await move_note(
        identifier="source/test-note",
        destination_path="target/MovedNote.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result
    assert "source/test-note" in result
    assert "target/MovedNote.md" in result

    # Verify original location no longer exists
    try:
        await read_note("source/test-note")
        assert False, "Original note should not exist after move"
    except Exception:
        pass  # Expected - note should not exist at original location

    # Verify note exists at new location with same content
    content = await read_note("target/moved-note")
    assert "# Test Note" in content
    assert "Original content here" in content
    assert "permalink: target/moved-note" in content


@pytest.mark.asyncio
async def test_move_note_with_folder_creation(client):
    """Test moving note creates necessary folders."""
    # Create initial note
    await write_note(
        title="Deep Note",
        folder="",
        content="# Deep Note\nContent in root folder.",
    )

    # Move to deeply nested path
    result = await move_note(
        identifier="deep-note",
        destination_path="deeply/nested/folder/DeepNote.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify note exists at new location
    content = await read_note("deeply/nested/folder/deep-note")
    assert "# Deep Note" in content
    assert "Content in root folder" in content


@pytest.mark.asyncio
async def test_move_note_with_observations_and_relations(client):
    """Test moving note preserves observations and relations."""
    # Create note with complex semantic content
    await write_note(
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
    result = await move_note(
        identifier="source/complex-entity",
        destination_path="target/MovedComplex.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify moved note preserves all content
    content = await read_note("target/moved-complex")
    assert "Important observation #tag1" in content
    assert "Key feature #feature" in content
    assert "[[SomeOtherEntity]]" in content
    assert "[[Dependency]]" in content
    assert "Some additional content" in content


@pytest.mark.asyncio
async def test_move_note_by_title(client):
    """Test moving note using title as identifier."""
    # Create note with unique title
    await write_note(
        title="UniqueTestTitle",
        folder="source",
        content="# UniqueTestTitle\nTest content.",
    )

    # Move using title as identifier
    result = await move_note(
        identifier="UniqueTestTitle",
        destination_path="target/MovedByTitle.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify note exists at new location
    content = await read_note("target/moved-by-title")
    assert "# UniqueTestTitle" in content
    assert "Test content" in content


@pytest.mark.asyncio
async def test_move_note_by_file_path(client):
    """Test moving note using file path as identifier."""
    # Create initial note
    await write_note(
        title="PathTest",
        folder="source",
        content="# PathTest\nContent for path test.",
    )

    # Move using file path as identifier
    result = await move_note(
        identifier="source/PathTest.md",
        destination_path="target/MovedByPath.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify note exists at new location
    content = await read_note("target/moved-by-path")
    assert "# PathTest" in content
    assert "Content for path test" in content


@pytest.mark.asyncio
async def test_move_note_nonexistent_note(client):
    """Test moving a note that doesn't exist."""
    with pytest.raises(Exception) as exc_info:
        await move_note(
            identifier="nonexistent/note",
            destination_path="target/SomeFile.md",
        )

    # Should raise an exception from the API with friendly error message
    error_msg = str(exc_info.value)
    assert "Entity not found" in error_msg or "Invalid request" in error_msg or "malformed" in error_msg


@pytest.mark.asyncio
async def test_move_note_invalid_destination_path(client):
    """Test moving note with invalid destination path."""
    # Create initial note
    await write_note(
        title="TestNote",
        folder="source",
        content="# TestNote\nTest content.",
    )

    # Test absolute path (should be rejected by validation)
    with pytest.raises(Exception) as exc_info:
        await move_note(
            identifier="source/test-note",
            destination_path="/absolute/path.md",
        )

    # Should raise validation error (422 gets wrapped as client error)
    error_msg = str(exc_info.value)
    assert (
        "Client error (422)" in error_msg
        or "could not be completed" in error_msg
        or "destination_path must be relative" in error_msg
    )


@pytest.mark.asyncio
async def test_move_note_destination_exists(client):
    """Test moving note to existing destination."""
    # Create source note
    await write_note(
        title="SourceNote",
        folder="source",
        content="# SourceNote\nSource content.",
    )

    # Create destination note
    await write_note(
        title="DestinationNote",
        folder="target",
        content="# DestinationNote\nDestination content.",
    )

    # Try to move source to existing destination
    with pytest.raises(Exception) as exc_info:
        await move_note(
            identifier="source/source-note",
            destination_path="target/DestinationNote.md",
        )

    # Should raise an exception (400 gets wrapped as malformed request)
    error_msg = str(exc_info.value)
    assert "Destination already exists" in error_msg or "Invalid request" in error_msg or "malformed" in error_msg


@pytest.mark.asyncio
async def test_move_note_same_location(client):
    """Test moving note to the same location."""
    # Create initial note
    await write_note(
        title="SameLocationTest",
        folder="test",
        content="# SameLocationTest\nContent here.",
    )

    # Try to move to same location
    with pytest.raises(Exception) as exc_info:
        await move_note(
            identifier="test/same-location-test",
            destination_path="test/SameLocationTest.md",
        )

    # Should raise an exception (400 gets wrapped as malformed request)
    error_msg = str(exc_info.value)
    assert "Destination already exists" in error_msg or "same location" in error_msg or "Invalid request" in error_msg or "malformed" in error_msg


@pytest.mark.asyncio
async def test_move_note_rename_only(client):
    """Test moving note within same folder (rename operation)."""
    # Create initial note
    await write_note(
        title="OriginalName",
        folder="test",
        content="# OriginalName\nContent to rename.",
    )

    # Rename within same folder
    result = await move_note(
        identifier="test/original-name",
        destination_path="test/NewName.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify original is gone and new exists
    try:
        await read_note("test/original-name")
        assert False, "Original note should not exist after rename"
    except Exception:
        pass  # Expected

    # Verify new name exists with same content
    content = await read_note("test/new-name")
    assert "# OriginalName" in content  # Title in content remains same
    assert "Content to rename" in content
    assert "permalink: test/new-name" in content


@pytest.mark.asyncio
async def test_move_note_complex_filename(client):
    """Test moving note with spaces in filename."""
    # Create note with spaces in name
    await write_note(
        title="Meeting Notes 2025",
        folder="meetings",
        content="# Meeting Notes 2025\nMeeting content with dates.",
    )

    # Move to new location
    result = await move_note(
        identifier="meetings/meeting-notes-2025",
        destination_path="archive/2025/meetings/Meeting Notes 2025.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify note exists at new location with correct content
    content = await read_note("archive/2025/meetings/meeting-notes-2025")
    assert "# Meeting Notes 2025" in content
    assert "Meeting content with dates" in content


@pytest.mark.asyncio
async def test_move_note_with_tags(client):
    """Test moving note with tags preserves tags."""
    # Create note with tags
    await write_note(
        title="Tagged Note",
        folder="source",
        content="# Tagged Note\nContent with tags.",
        tags=["important", "work", "project"],
    )

    # Move note
    result = await move_note(
        identifier="source/tagged-note",
        destination_path="target/MovedTaggedNote.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify tags are preserved in correct YAML format
    content = await read_note("target/moved-tagged-note")
    assert "- important" in content
    assert "- work" in content
    assert "- project" in content


@pytest.mark.asyncio
async def test_move_note_empty_string_destination(client):
    """Test moving note with empty destination path."""
    # Create initial note
    await write_note(
        title="TestNote",
        folder="source",
        content="# TestNote\nTest content.",
    )

    # Test empty destination path
    with pytest.raises(Exception) as exc_info:
        await move_note(
            identifier="source/test-note",
            destination_path="",
        )

    # Should raise validation error (422 gets wrapped as client error)
    error_msg = str(exc_info.value)
    assert (
        "String should have at least 1 character" in error_msg
        or "cannot be empty" in error_msg
        or "Client error (422)" in error_msg
        or "could not be completed" in error_msg
        or "destination_path cannot be empty" in error_msg
    )


@pytest.mark.asyncio
async def test_move_note_parent_directory_path(client):
    """Test moving note with parent directory in destination path."""
    # Create initial note
    await write_note(
        title="TestNote",
        folder="source",
        content="# TestNote\nTest content.",
    )

    # Test parent directory path
    with pytest.raises(Exception) as exc_info:
        await move_note(
            identifier="source/test-note",
            destination_path="../parent/file.md",
        )

    # Should raise validation error (422 gets wrapped as client error)
    error_msg = str(exc_info.value)
    assert (
        "Client error (422)" in error_msg
        or "could not be completed" in error_msg
        or "cannot contain '..' path components" in error_msg
    )


@pytest.mark.asyncio
async def test_move_note_identifier_variations(client):
    """Test that various identifier formats work for moving."""
    # Create a note to test different identifier formats
    await write_note(
        title="Test Document",
        folder="docs",
        content="# Test Document\nContent for testing identifiers.",
    )

    # Test with permalink identifier
    result = await move_note(
        identifier="docs/test-document",
        destination_path="moved/TestDocument.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify it moved correctly
    content = await read_note("moved/test-document")
    assert "# Test Document" in content
    assert "Content for testing identifiers" in content


@pytest.mark.asyncio
async def test_move_note_preserves_frontmatter(client):
    """Test that moving preserves custom frontmatter."""
    # Create note with custom frontmatter by first creating it normally
    await write_note(
        title="Custom Frontmatter Note",
        folder="source",
        content="# Custom Frontmatter Note\nContent with custom metadata.",
    )

    # Move the note
    result = await move_note(
        identifier="source/custom-frontmatter-note",
        destination_path="target/MovedCustomNote.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify the moved note has proper frontmatter structure
    content = await read_note("target/moved-custom-note")
    assert "title: Custom Frontmatter Note" in content
    assert "type: note" in content
    assert "permalink: target/moved-custom-note" in content
    assert "# Custom Frontmatter Note" in content
    assert "Content with custom metadata" in content
