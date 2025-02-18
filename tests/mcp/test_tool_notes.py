"""Tests for note tools that exercise the full stack with SQLite."""

from textwrap import dedent

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import notes


@pytest.mark.asyncio
async def test_write_note(app):
    """Test creating a new note.

    Should:
    - Create entity with correct type and content
    - Save markdown content
    - Handle tags correctly
    - Return valid permalink
    """
    result = await notes.write_note(
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
    content = await notes.read_note("test/test-note")
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
    result = await notes.write_note(title="Simple Note", folder="test", content="Just some text")

    assert result
    assert (
        dedent("""
        # Created test/Simple Note.md (9a1ff079)
        permalink: test/simple-note
        """).strip()
        in result
    )
    # Should be able to read it back
    content = await notes.read_note("test/simple-note")
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
    with pytest.raises(ToolError, match="Error calling tool: Client error '404 Not Found'"):
        await notes.read_note("notes/does-not-exist")


@pytest.mark.asyncio
async def test_write_note_update_existing(app):
    """Test creating a new note.

    Should:
    - Create entity with correct type and content
    - Save markdown content
    - Handle tags correctly
    - Return valid permalink
    """
    result = await notes.write_note(
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

    result = await notes.write_note(
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
    content = await notes.read_note("test/test-note")
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
    await notes.write_note(title="Special Note", folder="test", content="Note content here")

    # Should be able to read it by title
    content = await notes.read_note("Special Note")
    assert "Note content here" in content


@pytest.mark.asyncio
async def test_note_unicode_content(app):
    """Test handling of unicode content in notes."""
    content = "# Test 🚀\nThis note has emoji 🎉 and unicode ♠♣♥♦"
    result = await notes.write_note(title="Unicode Test", folder="test", content=content)

    assert (
        dedent("""
        # Created test/Unicode Test.md (272389cd)
        permalink: test/unicode-test
        """).strip()
        in result
    )

    # Read back should preserve unicode
    result = await notes.read_note("test/unicode-test")
    assert content in result


@pytest.mark.asyncio
async def test_multiple_notes(app):
    """Test creating and managing multiple notes."""
    # Create several notes
    notes_data = [
        ("test/note-1", "Note 1", "test", "Content 1", ["tag1"]),
        ("test/note-2", "Note 2", "test", "Content 2", ["tag1", "tag2"]),
        ("test/note-3", "Note 3", "test", "Content 3", []),
    ]

    for _, title, folder, content, tags in notes_data:
        await notes.write_note(title=title, folder=folder, content=content, tags=tags)

    # Should be able to read each one
    for permalink, title, folder, content, _ in notes_data:
        note = await notes.read_note(permalink)
        assert content in note

    # read multiple notes at once

    result = await notes.read_note("test/*")

    # note we can't compare times
    assert "--- memory://test/note-1" in result
    assert "Content 1" in result

    assert "--- memory://test/note-2" in result
    assert "Content 2" in result

    assert "--- memory://test/note-3" in result
    assert "Content 3" in result


@pytest.mark.asyncio
async def test_delete_note_existing(app):
    """Test deleting a new note.

    Should:
    - Create entity with correct type and content
    - Return valid permalink
    - Delete the note
    """
    result = await notes.write_note(
        title="Test Note",
        folder="test",
        content="# Test\nThis is a test note",
        tags=["test", "documentation"],
    )

    assert result

    deleted = await notes.delete_note("test/test-note")
    assert deleted is True


@pytest.mark.asyncio
async def test_delete_note_doesnt_exist(app):
    """Test deleting a new note.

    Should:
    - Delete the note
    - verify returns false
    """
    deleted = await notes.delete_note("doesnt-exist")
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
    result = await notes.write_note(
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
    result = await notes.write_note(
        title="Memory URL Test",
        folder="test",
        content="Testing memory:// URL handling",
    )
    assert result

    # Should be able to read it with a memory:// URL
    memory_url = "memory://test/memory-url-test"
    content = await notes.read_note(memory_url)
    assert "Testing memory:// URL handling" in content
