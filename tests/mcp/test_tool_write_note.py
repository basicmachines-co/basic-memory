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
        # Created note
        file_path: test/Test Note.md
        permalink: test/test-note
        checksum: 159f2168
        
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
        # Created note
        file_path: test/Simple Note.md
        permalink: test/simple-note
        checksum: 9a1ff079
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
        # Created note
        file_path: test/Test Note.md
        permalink: test/test-note
        checksum: 159f2168
        
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
        # Updated note
        file_path: test/Test Note.md
        permalink: test/test-note
        checksum: a8eb4d44
        
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
async def test_write_note_with_tag_array_from_bug_report(app):
    """Test creating a note with a tag array as reported in issue #38.

    This reproduces the exact payload from the bug report where Cursor
    was passing an array of tags and getting a type mismatch error.
    """
    # This is the exact payload from the bug report
    bug_payload = {
        "title": "Title",
        "folder": "folder",
        "content": "CONTENT",
        "tags": ["hipporag", "search", "fallback", "symfony", "error-handling"],
    }

    # Try to call the function with this data directly
    result = await write_note(**bug_payload)

    assert result
    assert "permalink: folder/title" in result
    assert "Tags" in result
    assert "hipporag" in result


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
        # Created note
        file_path: test/Test Note.md
        permalink: test/test-note
        checksum: 06873a7a
        
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
async def test_write_note_preserves_custom_metadata(app, test_config):
    """Test that updating a note preserves custom metadata fields.

    Reproduces issue #36 where custom frontmatter fields like Status
    were being lost when updating notes with the write_note tool.

    Should:
    - Create a note with custom frontmatter
    - Update the note with new content
    - Verify custom frontmatter is preserved
    """
    # First, create a note with custom metadata using write_note
    await write_note(
        title="Custom Metadata Note",
        folder="test",
        content="# Initial content",
        tags=["test"],
    )

    # Read the note to get its permalink
    content = await read_note("test/custom-metadata-note")

    # Now directly update the file with custom frontmatter
    # We need to use a direct file update to add custom frontmatter
    import frontmatter

    file_path = test_config.home / "test" / "Custom Metadata Note.md"
    post = frontmatter.load(file_path)

    # Add custom frontmatter
    post["Status"] = "In Progress"
    post["Priority"] = "High"
    post["Version"] = "1.0"

    # Write the file back
    with open(file_path, "w") as f:
        f.write(frontmatter.dumps(post))

    # Now update the note using write_note
    result = await write_note(
        title="Custom Metadata Note",
        folder="test",
        content="# Updated content",
        tags=["test", "updated"],
    )

    # Verify the update was successful
    assert ("Updated note\nfile_path: test/Custom Metadata Note.md") in result

    # Read the note back and check if custom frontmatter is preserved
    content = await read_note("test/custom-metadata-note")

    # Custom frontmatter should be preserved
    assert "Status: In Progress" in content
    assert "Priority: High" in content
    # Version might be quoted as '1.0' due to YAML serialization
    assert "Version:" in content  # Just check that the field exists
    assert "1.0" in content  # And that the value exists somewhere

    # And new content should be there
    assert "# Updated content" in content

    # And tags should be updated
    assert "'#test'" in content
    assert "'#updated'" in content
