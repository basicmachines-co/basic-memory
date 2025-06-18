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
    result = await write_note.fn(
        title="Test Note",
        folder="test",
        content="# Test\nThis is a test note",
        tags=["test", "documentation"],
    )

    assert result
    assert "# Created note" in result
    assert "file_path: test/Test Note.md" in result
    assert "permalink: test/test-note" in result
    assert "## Tags" in result
    assert "- test, documentation" in result

    # Try reading it back via permalink
    content = await read_note.fn("test/test-note")
    assert (
        dedent("""
        ---
        title: Test Note
        type: note
        permalink: test/test-note
        tags:
        - test
        - documentation
        ---
        
        # Test
        This is a test note
        """).strip()
        in content
    )


@pytest.mark.asyncio
async def test_write_note_no_tags(app):
    """Test creating a note without tags."""
    result = await write_note.fn(title="Simple Note", folder="test", content="Just some text")

    assert result
    assert "# Created note" in result
    assert "file_path: test/Simple Note.md" in result
    assert "permalink: test/simple-note" in result
    # Should be able to read it back
    content = await read_note.fn("test/simple-note")
    assert (
        dedent("""
        ---
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
    result = await write_note.fn(
        title="Test Note",
        folder="test",
        content="# Test\nThis is a test note",
        tags=["test", "documentation"],
    )

    assert result  # Got a valid permalink
    assert "# Created note" in result
    assert "file_path: test/Test Note.md" in result
    assert "permalink: test/test-note" in result
    assert "## Tags" in result
    assert "- test, documentation" in result

    result = await write_note.fn(
        title="Test Note",
        folder="test",
        content="# Test\nThis is an updated note",
        tags=["test", "documentation"],
    )
    assert "# Updated note" in result
    assert "file_path: test/Test Note.md" in result
    assert "permalink: test/test-note" in result
    assert "## Tags" in result
    assert "- test, documentation" in result

    # Try reading it back
    content = await read_note.fn("test/test-note")
    assert (
        dedent(
            """
        ---
        title: Test Note
        type: note
        permalink: test/test-note
        tags:
        - test
        - documentation
        ---
        
        # Test
        This is an updated note
        """
        ).strip()
        == content
    )


@pytest.mark.asyncio
async def test_issue_93_write_note_respects_custom_permalink_new_note(app):
    """Test that write_note respects custom permalinks in frontmatter for new notes (Issue #93)"""

    # Create a note with custom permalink in frontmatter
    content_with_custom_permalink = dedent("""
        ---
        permalink: custom/my-desired-permalink  
        ---
        
        # My New Note
        
        This note has a custom permalink specified in frontmatter.
        
        - [note] Testing if custom permalink is respected
    """).strip()

    result = await write_note.fn(
        title="My New Note",
        folder="notes",
        content=content_with_custom_permalink,
    )

    # Verify the custom permalink is respected
    assert "# Created note" in result
    assert "file_path: notes/My New Note.md" in result
    assert "permalink: custom/my-desired-permalink" in result


@pytest.mark.asyncio
async def test_issue_93_write_note_respects_custom_permalink_existing_note(app):
    """Test that write_note respects custom permalinks when updating existing notes (Issue #93)"""

    # Step 1: Create initial note (auto-generated permalink)
    result1 = await write_note.fn(
        title="Existing Note",
        folder="test",
        content="Initial content without custom permalink",
    )

    assert "# Created note" in result1

    # Extract the auto-generated permalink
    initial_permalink = None
    for line in result1.split("\n"):
        if line.startswith("permalink:"):
            initial_permalink = line.split(":", 1)[1].strip()
            break

    assert initial_permalink is not None

    # Step 2: Update with content that includes custom permalink in frontmatter
    updated_content = dedent("""
        ---
        permalink: custom/new-permalink
        ---
        
        # Existing Note
        
        Updated content with custom permalink in frontmatter.
        
        - [note] Custom permalink should be respected on update
    """).strip()

    result2 = await write_note.fn(
        title="Existing Note",
        folder="test",
        content=updated_content,
    )

    # Verify the custom permalink is respected
    assert "# Updated note" in result2
    assert "permalink: custom/new-permalink" in result2
    assert f"permalink: {initial_permalink}" not in result2


@pytest.mark.asyncio
async def test_delete_note_existing(app):
    """Test deleting a new note.

    Should:
    - Create entity with correct type and content
    - Return valid permalink
    - Delete the note
    """
    result = await write_note.fn(
        title="Test Note",
        folder="test",
        content="# Test\nThis is a test note",
        tags=["test", "documentation"],
    )

    assert result

    deleted = await delete_note.fn("test/test-note")
    assert deleted is True


@pytest.mark.asyncio
async def test_delete_note_doesnt_exist(app):
    """Test deleting a new note.

    Should:
    - Delete the note
    - verify returns false
    """
    deleted = await delete_note.fn("doesnt-exist")
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
    result = await write_note.fn(**bug_payload)

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
    result = await write_note.fn(
        title="Test Note",
        folder="test",
        content="""
# Test\nThis is a test note

- [note] First observation
- relates to [[Knowledge]]

""",
        tags=["test", "documentation"],
    )

    assert "# Created note" in result
    assert "file_path: test/Test Note.md" in result
    assert "permalink: test/test-note" in result
    assert "## Observations" in result
    assert "- note: 1" in result
    assert "## Relations" in result
    assert "## Tags" in result
    assert "- test, documentation" in result


@pytest.mark.asyncio
async def test_write_note_preserves_custom_metadata(app, project_config):
    """Test that updating a note preserves custom metadata fields.

    Reproduces issue #36 where custom frontmatter fields like Status
    were being lost when updating notes with the write_note tool.

    Should:
    - Create a note with custom frontmatter
    - Update the note with new content
    - Verify custom frontmatter is preserved
    """
    # First, create a note with custom metadata using write_note
    await write_note.fn(
        title="Custom Metadata Note",
        folder="test",
        content="# Initial content",
        tags=["test"],
    )

    # Read the note to get its permalink
    content = await read_note.fn("test/custom-metadata-note")

    # Now directly update the file with custom frontmatter
    # We need to use a direct file update to add custom frontmatter
    import frontmatter

    file_path = project_config.home / "test" / "Custom Metadata Note.md"
    post = frontmatter.load(file_path)

    # Add custom frontmatter
    post["Status"] = "In Progress"
    post["Priority"] = "High"
    post["Version"] = "1.0"

    # Write the file back
    with open(file_path, "w") as f:
        f.write(frontmatter.dumps(post))

    # Now update the note using write_note
    result = await write_note.fn(
        title="Custom Metadata Note",
        folder="test",
        content="# Updated content",
        tags=["test", "updated"],
    )

    # Verify the update was successful
    assert ("Updated note\nfile_path: test/Custom Metadata Note.md") in result

    # Read the note back and check if custom frontmatter is preserved
    content = await read_note.fn("test/custom-metadata-note")

    # Custom frontmatter should be preserved
    assert "Status: In Progress" in content
    assert "Priority: High" in content
    # Version might be quoted as '1.0' due to YAML serialization
    assert "Version:" in content  # Just check that the field exists
    assert "1.0" in content  # And that the value exists somewhere

    # And new content should be there
    assert "# Updated content" in content

    # And tags should be updated (without # prefix)
    assert "- test" in content
    assert "- updated" in content


@pytest.mark.asyncio
async def test_write_note_preserves_content_frontmatter(app):
    """Test creating a new note."""
    await write_note.fn(
        title="Test Note",
        folder="test",
        content=dedent(
            """
            ---
            title: Test Note
            type: note
            version: 1.0 
            author: name
            ---
            # Test
            
            This is a test note
            """
        ),
        tags=["test", "documentation"],
    )

    # Try reading it back via permalink
    content = await read_note.fn("test/test-note")
    assert (
        dedent(
            """
            ---
            title: Test Note
            type: note
            permalink: test/test-note
            version: 1.0
            author: name
            tags:
            - test
            - documentation
            ---
            
            # Test
            
            This is a test note
            """
        ).strip()
        in content
    )


@pytest.mark.asyncio
async def test_write_note_permalink_collision_fix_issue_139(app):
    """Test fix for GitHub Issue #139: UNIQUE constraint failed: entity.permalink.
    
    This reproduces the exact scenario described in the issue:
    1. Create a note with title "Note 1" 
    2. Create another note with title "Note 2"
    3. Try to create/replace first note again with same title "Note 1"
    
    Before the fix, step 3 would fail with UNIQUE constraint error.
    After the fix, it should either update the existing note or create with unique permalink.
    """
    # Step 1: Create first note
    result1 = await write_note.fn(
        title="Note 1",
        folder="test",
        content="Original content for note 1"
    )
    assert "# Created note" in result1
    assert "permalink: test/note-1" in result1
    
    # Step 2: Create second note with different title
    result2 = await write_note.fn(
        title="Note 2", 
        folder="test",
        content="Content for note 2"
    )
    assert "# Created note" in result2
    assert "permalink: test/note-2" in result2
    
    # Step 3: Try to create/replace first note again
    # This scenario would trigger the UNIQUE constraint failure before the fix
    result3 = await write_note.fn(
        title="Note 1",  # Same title as first note
        folder="test",   # Same folder as first note
        content="Replacement content for note 1"  # Different content
    )
    
    # This should not raise a UNIQUE constraint failure error
    # It should succeed and either:
    # 1. Update the existing note (preferred behavior)
    # 2. Create a new note with unique permalink (fallback behavior)
    
    assert result3 is not None
    assert ("Updated note" in result3 or "Created note" in result3)
    
    # The result should contain either the original permalink or a unique one
    assert ("permalink: test/note-1" in result3 or "permalink: test/note-1-1" in result3)
    
    # Verify we can read back the content
    if "permalink: test/note-1" in result3:
        # Updated existing note case
        content = await read_note.fn("test/note-1")
        assert "Replacement content for note 1" in content
    else:
        # Created new note with unique permalink case  
        content = await read_note.fn("test/note-1-1")
        assert "Replacement content for note 1" in content
        # Original note should still exist
        original_content = await read_note.fn("test/note-1")
        assert "Original content for note 1" in original_content


@pytest.mark.asyncio
async def test_write_note_with_custom_entity_type(app):
    """Test creating a note with custom entity_type parameter.

    This test verifies the fix for Issue #144 where entity_type parameter
    was hardcoded to "note" instead of allowing custom types.
    """
    result = await write_note.fn(
        title="Test Guide",
        folder="guides",
        content="# Guide Content\nThis is a guide",
        tags=["guide", "documentation"],
        entity_type="guide",
    )

    assert result
    assert "# Created note" in result
    assert "file_path: guides/Test Guide.md" in result
    assert "permalink: guides/test-guide" in result
    assert "## Tags" in result
    assert "- guide, documentation" in result

    # Verify the entity type is correctly set in the frontmatter
    content = await read_note.fn("guides/test-guide")
    assert (
        dedent("""
        ---
        title: Test Guide
        type: guide
        permalink: guides/test-guide
        tags:
        - guide
        - documentation
        ---
        
        # Guide Content
        This is a guide
        """).strip()
        in content
    )


@pytest.mark.asyncio
async def test_write_note_with_report_entity_type(app):
    """Test creating a note with entity_type="report"."""
    result = await write_note.fn(
        title="Monthly Report",
        folder="reports",
        content="# Monthly Report\nThis is a monthly report",
        tags=["report", "monthly"],
        entity_type="report",
    )

    assert result
    assert "# Created note" in result
    assert "file_path: reports/Monthly Report.md" in result
    assert "permalink: reports/monthly-report" in result

    # Verify the entity type is correctly set in the frontmatter
    content = await read_note.fn("reports/monthly-report")
    assert "type: report" in content
    assert "# Monthly Report" in content


@pytest.mark.asyncio
async def test_write_note_with_config_entity_type(app):
    """Test creating a note with entity_type="config"."""
    result = await write_note.fn(
        title="System Config",
        folder="config",
        content="# System Configuration\nThis is a config file",
        entity_type="config",
    )

    assert result
    assert "# Created note" in result
    assert "file_path: config/System Config.md" in result
    assert "permalink: config/system-config" in result

    # Verify the entity type is correctly set in the frontmatter
    content = await read_note.fn("config/system-config")
    assert "type: config" in content
    assert "# System Configuration" in content


@pytest.mark.asyncio
async def test_write_note_entity_type_default_behavior(app):
    """Test that the entity_type parameter defaults to "note" when not specified.

    This ensures backward compatibility - existing code that doesn't specify
    entity_type should continue to work as before.
    """
    result = await write_note.fn(
        title="Default Type Test",
        folder="test",
        content="# Default Type Test\nThis should be type 'note'",
        tags=["test"],
    )

    assert result
    assert "# Created note" in result
    assert "file_path: test/Default Type Test.md" in result
    assert "permalink: test/default-type-test" in result

    # Verify the entity type defaults to "note"
    content = await read_note.fn("test/default-type-test")
    assert "type: note" in content
    assert "# Default Type Test" in content


@pytest.mark.asyncio
async def test_write_note_update_existing_with_different_entity_type(app):
    """Test updating an existing note with a different entity_type."""
    # Create initial note as "note" type
    result1 = await write_note.fn(
        title="Changeable Type",
        folder="test",
        content="# Initial Content\nThis starts as a note",
        tags=["test"],
        entity_type="note",
    )

    assert result1
    assert "# Created note" in result1

    # Update the same note with a different entity_type
    result2 = await write_note.fn(
        title="Changeable Type",
        folder="test",
        content="# Updated Content\nThis is now a guide",
        tags=["guide"],
        entity_type="guide",
    )

    assert result2
    assert "# Updated note" in result2

    # Verify the entity type was updated
    content = await read_note.fn("test/changeable-type")
    assert "type: guide" in content
    assert "# Updated Content" in content
    assert "- guide" in content


@pytest.mark.asyncio
async def test_write_note_respects_frontmatter_entity_type(app):
    """Test that entity_type in frontmatter is respected when parameter is not provided.

    This verifies that when write_note is called without entity_type parameter,
    but the content includes frontmatter with a 'type' field, that type is respected
    instead of defaulting to 'note'.
    """
    note = dedent("""
        ---
        title: Test Guide
        type: guide
        permalink: guides/test-guide
        tags:
        - guide
        - documentation
        ---
        
        # Guide Content
        This is a guide
        """).strip()

    # Call write_note without entity_type parameter - it should respect frontmatter type
    result = await write_note.fn(title="Test Guide", folder="guides", content=note)

    assert result
    assert "# Created note" in result
    assert "file_path: guides/Test Guide.md" in result
    assert "permalink: guides/test-guide" in result

    # Verify the entity type from frontmatter is respected (should be "guide", not "note")
    content = await read_note.fn("guides/test-guide")
    assert "type: guide" in content
    assert "# Guide Content" in content
    assert "- guide" in content
    assert "- documentation" in content
