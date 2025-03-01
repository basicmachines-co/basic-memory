"""Tests for note tools that exercise the full stack with SQLite."""

from textwrap import dedent

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import write_note, read_note, delete_note


@pytest.mark.asyncio
async def test_write_note(app):
    """Test creating a new note.

    Should:
    - Create entity with correct type and content
    - Save markdown content
    - Handle tags correctly
    - Return valid permalink
    """
    result = await write_note(
        title="Test Note",
        folder="test",
        content="# Test\nThis is a test note",
        tags=["test", "documentation"],
    )

    assert result
    assert (
        dedent("""
        # Created test/Test Note.md (159f2168)
        permalink: test/test-note
        
        ## Tags
        - test, documentation
        """).strip()
        in result
    )

    # Try reading it back via permalink
    content = await read_note("test/test-note")
    assert (
        dedent("""
        ---
        title: Test Note
        type: note
        permalink: test/test-note
        tags:
        - '#test'
        - '#documentation'
        ---
        
        # Test
        This is a test note
        """).strip()
        in content
    )


@pytest.mark.asyncio
async def test_write_note_no_tags(app):
    """Test creating a note without tags."""
    result = await write_note(title="Simple Note", folder="test", content="Just some text")

    assert result
    assert (
        dedent("""
        # Created test/Simple Note.md (9a1ff079)
        permalink: test/simple-note
        """).strip()
        in result
    )
    # Should be able to read it back
    content = await read_note("test/simple-note")
    assert (
        dedent("""
        --
        title: Simple Note
        type: note
        permalink: test/simple-note
        ---
        
        Just some text
        """).strip()
        in content
    )


@pytest.mark.asyncio
async def test_read_note_not_found(app):
    """Test trying to read a non-existent note."""
    with pytest.raises(ToolError, match="Resource not found"):
        await read_note("notes/does-not-exist")


@pytest.mark.asyncio
async def test_write_note_update_existing(app):
    """Test creating a new note.

    Should:
    - Create entity with correct type and content
    - Save markdown content
    - Handle tags correctly
    - Return valid permalink
    """
    result = await write_note(
        title="Test Note",
        folder="test",
        content="# Test\nThis is a test note",
        tags=["test", "documentation"],
    )

    assert result  # Got a valid permalink
    assert (
        dedent("""
        # Created test/Test Note.md (159f2168)
        permalink: test/test-note
        
        ## Tags
        - test, documentation
        """).strip()
        in result
    )

    result = await write_note(
        title="Test Note",
        folder="test",
        content="# Test\nThis is an updated note",
        tags=["test", "documentation"],
    )
    assert (
        dedent("""
        # Updated test/Test Note.md (131b5662)
        permalink: test/test-note
        
        ## Tags
        - test, documentation
        """).strip()
        in result
    )

    # Try reading it back
    content = await read_note("test/test-note")
    assert (
        """
---
permalink: test/test-note
tags:
- '#test'
- '#documentation'
title: Test Note
type: note
---

# Test
This is an updated note
""".strip()
        in content
    )


@pytest.mark.asyncio
async def test_read_note_by_title(app):
    """Test reading a note by its title."""
    # First create a note
    await write_note(title="Special Note", folder="test", content="Note content here")

    # Should be able to read it by title
    content = await read_note("Special Note")
    assert "Note content here" in content


@pytest.mark.asyncio
async def test_note_unicode_content(app):
    """Test handling of unicode content in"""
    content = "# Test 🚀\nThis note has emoji 🎉 and unicode ♠♣♥♦"
    result = await write_note(title="Unicode Test", folder="test", content=content)

    assert (
        dedent("""
        # Created test/Unicode Test.md (272389cd)
        permalink: test/unicode-test
        """).strip()
        in result
    )

    # Read back should preserve unicode
    result = await read_note("test/unicode-test")
    assert content in result


@pytest.mark.asyncio
async def test_multiple_notes(app):
    """Test creating and managing multiple"""
    # Create several notes
    notes_data = [
        ("test/note-1", "Note 1", "test", "Content 1", ["tag1"]),
        ("test/note-2", "Note 2", "test", "Content 2", ["tag1", "tag2"]),
        ("test/note-3", "Note 3", "test", "Content 3", []),
    ]

    for _, title, folder, content, tags in notes_data:
        await write_note(title=title, folder=folder, content=content, tags=tags)

    # Should be able to read each one
    for permalink, title, folder, content, _ in notes_data:
        note = await read_note(permalink)
        assert content in note

    # read multiple notes at once

    result = await read_note("test/*")

    # note we can't compare times
    assert "--- memory://test/note-1" in result
    assert "Content 1" in result

    assert "--- memory://test/note-2" in result
    assert "Content 2" in result

    assert "--- memory://test/note-3" in result
    assert "Content 3" in result


@pytest.mark.asyncio
async def test_multiple_notes_pagination(app):
    """Test creating and managing multiple"""
    # Create several notes
    notes_data = [
        ("test/note-1", "Note 1", "test", "Content 1", ["tag1"]),
        ("test/note-2", "Note 2", "test", "Content 2", ["tag1", "tag2"]),
        ("test/note-3", "Note 3", "test", "Content 3", []),
    ]

    for _, title, folder, content, tags in notes_data:
        await write_note(title=title, folder=folder, content=content, tags=tags)

    # Should be able to read each one
    for permalink, title, folder, content, _ in notes_data:
        note = await read_note(permalink)
        assert content in note

    # read multiple notes at once with pagination
    result = await read_note("test/*", page=1, page_size=2)

    # note we can't compare times
    assert "--- memory://test/note-1" in result
    assert "Content 1" in result

    assert "--- memory://test/note-2" in result
    assert "Content 2" in result


@pytest.mark.asyncio
async def test_delete_note_existing(app):
    """Test deleting a new note.

    Should:
    - Create entity with correct type and content
    - Return valid permalink
    - Delete the note
    """
    result = await write_note(
        title="Test Note",
        folder="test",
        content="# Test\nThis is a test note",
        tags=["test", "documentation"],
    )

    assert result

    deleted = await delete_note("test/test-note")
    assert deleted is True


@pytest.mark.asyncio
async def test_delete_note_doesnt_exist(app):
    """Test deleting a new note.

    Should:
    - Delete the note
    - verify returns false
    """
    deleted = await delete_note("doesnt-exist")
    assert deleted is False


@pytest.mark.asyncio
async def test_write_note_verbose(app):
    """Test creating a new note.

    Should:
    - Create entity with correct type and content
    - Save markdown content
    - Handle tags correctly
    - Return valid permalink
    """
    result = await write_note(
        title="Test Note",
        folder="test",
        content="""
# Test\nThis is a test note

- [note] First observation
- relates to [[Knowledge]]

""",
        tags=["test", "documentation"],
    )

    assert (
        dedent("""
        # Created test/Test Note.md (06873a7a)
        permalink: test/test-note
        
        ## Observations
        - note: 1
        
        ## Relations
        - Resolved: 0
        - Unresolved: 1
        
        Unresolved relations will be retried on next sync.
        
        ## Tags
        - test, documentation
        """).strip()
        in result
    )


@pytest.mark.asyncio
async def test_read_note_memory_url(app):
    """Test reading a note using a memory:// URL.

    Should:
    - Handle memory:// URLs correctly
    - Normalize the URL before resolving
    - Return the note content
    """
    # First create a note
    result = await write_note(
        title="Memory URL Test",
        folder="test",
        content="Testing memory:// URL handling",
    )
    assert result

    # Should be able to read it with a memory:// URL
    memory_url = "memory://test/memory-url-test"
    content = await read_note(memory_url)
    assert "Testing memory:// URL handling" in content


@pytest.mark.asyncio
async def test_read_note_non_error_status(app, mocker):
    """Test scenario where read_note gets a non-200 status code that doesn't raise an exception.

    This tests the specific path that returns an error message for non-200 status
    when we don't have an exception.
    """
    # Create a mock response with a non-200 status that doesn't raise an exception
    mock_response = mocker.MagicMock()
    mock_response.status_code = 204  # No content

    # Mock the call_get function to return our mock response
    mocker.patch("basic_memory.mcp.tools.read_note.call_get", return_value=mock_response)

    # Call read_note which should hit our error message path
    result = await read_note("test/non-existing-note")

    # Verify the error message format
    assert result == "Error: Could not find entity at test/non-existing-note"
