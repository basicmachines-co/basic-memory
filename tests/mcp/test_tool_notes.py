"""Tests for note tools that exercise the full stack with SQLite."""

from textwrap import dedent

import pytest

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
        # Updated test/Test Note.md (a8eb4d44)
        permalink: test/test-note
        
        ## Tags
        - test, documentation
        """).strip()
        in result
    )

    # Try reading it back
    content = await read_note("test/test-note")
    assert (
        dedent(
            """
        ---
        title: Test Note
        type: note
        permalink: test/test-note
        tags:
        - '#test'
        - '#documentation'
        ---
        
        # Test
        This is an updated note
        """
        ).strip()
        == content
    )


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
