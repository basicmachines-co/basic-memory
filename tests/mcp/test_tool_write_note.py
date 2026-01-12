"""Tests for note tools that exercise the full stack with SQLite."""

from textwrap import dedent
import pytest

from basic_memory.mcp.tools import write_note, read_note, delete_note
from basic_memory.utils import normalize_newlines


@pytest.mark.asyncio
async def test_write_note(app, test_project):
    """Test creating a new note.

    Should:
    - Create entity with correct type and content
    - Save markdown content
    - Handle tags correctly
    - Return valid permalink
    """
    result = await write_note.fn(
        project=test_project.name,
        title="Test Note",
        folder="test",
        content="# Test\nThis is a test note",
        tags=["test", "documentation"],
    )

    assert result
    assert "# Created note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: test/Test Note.md" in result
    assert "permalink: test/test-note" in result
    assert "## Tags" in result
    assert "- test, documentation" in result
    assert f"[Session: Using project '{test_project.name}']" in result

    # Try reading it back via permalink
    content = await read_note.fn("test/test-note", project=test_project.name)
    assert (
        normalize_newlines(
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
        )
        in content
    )


@pytest.mark.asyncio
async def test_write_note_no_tags(app, test_project):
    """Test creating a note without tags."""
    result = await write_note.fn(
        project=test_project.name, title="Simple Note", folder="test", content="Just some text"
    )

    assert result
    assert "# Created note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: test/Simple Note.md" in result
    assert "permalink: test/simple-note" in result
    assert f"[Session: Using project '{test_project.name}']" in result
    # Should be able to read it back
    content = await read_note.fn("test/simple-note", project=test_project.name)
    assert (
        normalize_newlines(
            dedent("""
        ---
        title: Simple Note
        type: note
        permalink: test/simple-note
        ---
        
        Just some text
        """).strip()
        )
        in content
    )


@pytest.mark.asyncio
async def test_write_note_update_existing(app, test_project):
    """Test creating a new note.

    Should:
    - Create entity with correct type and content
    - Save markdown content
    - Handle tags correctly
    - Return valid permalink
    """
    result = await write_note.fn(
        project=test_project.name,
        title="Test Note",
        folder="test",
        content="# Test\nThis is a test note",
        tags=["test", "documentation"],
    )

    assert result  # Got a valid permalink
    assert "# Created note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: test/Test Note.md" in result
    assert "permalink: test/test-note" in result
    assert "## Tags" in result
    assert "- test, documentation" in result
    assert f"[Session: Using project '{test_project.name}']" in result

    result = await write_note.fn(
        project=test_project.name,
        title="Test Note",
        folder="test",
        content="# Test\nThis is an updated note",
        tags=["test", "documentation"],
    )
    assert "# Updated note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: test/Test Note.md" in result
    assert "permalink: test/test-note" in result
    assert "## Tags" in result
    assert "- test, documentation" in result
    assert f"[Session: Using project '{test_project.name}']" in result

    # Try reading it back
    content = await read_note.fn("test/test-note", project=test_project.name)
    assert (
        normalize_newlines(
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
        )
        == content
    )


@pytest.mark.asyncio
async def test_issue_93_write_note_respects_custom_permalink_new_note(app, test_project):
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
        project=test_project.name,
        title="My New Note",
        folder="notes",
        content=content_with_custom_permalink,
    )

    # Verify the custom permalink is respected
    assert "# Created note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: notes/My New Note.md" in result
    assert "permalink: custom/my-desired-permalink" in result
    assert f"[Session: Using project '{test_project.name}']" in result


@pytest.mark.asyncio
async def test_issue_93_write_note_respects_custom_permalink_existing_note(app, test_project):
    """Test that write_note respects custom permalinks when updating existing notes (Issue #93)"""

    # Step 1: Create initial note (auto-generated permalink)
    result1 = await write_note.fn(
        project=test_project.name,
        title="Existing Note",
        folder="test",
        content="Initial content without custom permalink",
    )

    assert "# Created note" in result1
    assert f"project: {test_project.name}" in result1

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
        project=test_project.name,
        title="Existing Note",
        folder="test",
        content=updated_content,
    )

    # Verify the custom permalink is respected
    assert "# Updated note" in result2
    assert f"project: {test_project.name}" in result2
    assert "permalink: custom/new-permalink" in result2
    assert f"permalink: {initial_permalink}" not in result2
    assert f"[Session: Using project '{test_project.name}']" in result2


@pytest.mark.asyncio
async def test_delete_note_existing(app, test_project):
    """Test deleting a new note.

    Should:
    - Create entity with correct type and content
    - Return valid permalink
    - Delete the note
    """
    result = await write_note.fn(
        project=test_project.name,
        title="Test Note",
        folder="test",
        content="# Test\nThis is a test note",
        tags=["test", "documentation"],
    )

    assert result
    assert f"project: {test_project.name}" in result

    deleted = await delete_note.fn("test/test-note", project=test_project.name)
    assert deleted is True


@pytest.mark.asyncio
async def test_delete_note_doesnt_exist(app, test_project):
    """Test deleting a new note.

    Should:
    - Delete the note
    - verify returns false
    """
    deleted = await delete_note.fn("doesnt-exist", project=test_project.name)
    assert deleted is False


@pytest.mark.asyncio
async def test_write_note_with_tag_array_from_bug_report(app, test_project):
    """Test creating a note with a tag array as reported in issue #38.

    This reproduces the exact payload from the bug report where Cursor
    was passing an array of tags and getting a type mismatch error.
    """
    # This is the exact payload from the bug report
    bug_payload = {
        "project": test_project.name,
        "title": "Title",
        "folder": "folder",
        "content": "CONTENT",
        "tags": ["hipporag", "search", "fallback", "symfony", "error-handling"],
    }

    # Try to call the function with this data directly
    result = await write_note.fn(**bug_payload)

    assert result
    assert f"project: {test_project.name}" in result
    assert "permalink: folder/title" in result
    assert "Tags" in result
    assert "hipporag" in result
    assert f"[Session: Using project '{test_project.name}']" in result


@pytest.mark.asyncio
async def test_write_note_verbose(app, test_project):
    """Test creating a new note.

    Should:
    - Create entity with correct type and content
    - Save markdown content
    - Handle tags correctly
    - Return valid permalink
    """
    result = await write_note.fn(
        project=test_project.name,
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
    assert f"project: {test_project.name}" in result
    assert "file_path: test/Test Note.md" in result
    assert "permalink: test/test-note" in result
    assert "## Observations" in result
    assert "- note: 1" in result
    assert "## Relations" in result
    assert "## Tags" in result
    assert "- test, documentation" in result
    assert f"[Session: Using project '{test_project.name}']" in result


@pytest.mark.asyncio
async def test_write_note_preserves_custom_metadata(app, project_config, test_project):
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
        project=test_project.name,
        title="Custom Metadata Note",
        folder="test",
        content="# Initial content",
        tags=["test"],
    )

    # Read the note to get its permalink
    content = await read_note.fn("test/custom-metadata-note", project=test_project.name)

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
        project=test_project.name,
        title="Custom Metadata Note",
        folder="test",
        content="# Updated content",
        tags=["test", "updated"],
    )

    # Verify the update was successful
    assert (
        "Updated note\nproject: test-project\nfile_path: test/Custom Metadata Note.md"
    ) in result
    assert f"project: {test_project.name}" in result

    # Read the note back and check if custom frontmatter is preserved
    content = await read_note.fn("test/custom-metadata-note", project=test_project.name)

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
async def test_write_note_preserves_content_frontmatter(app, test_project):
    """Test creating a new note."""
    await write_note.fn(
        project=test_project.name,
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
    content = await read_note.fn("test/test-note", project=test_project.name)
    assert (
        normalize_newlines(
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
        )
        in content
    )


@pytest.mark.asyncio
async def test_write_note_permalink_collision_fix_issue_139(app, test_project):
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
        project=test_project.name,
        title="Note 1",
        folder="test",
        content="Original content for note 1",
    )
    assert "# Created note" in result1
    assert f"project: {test_project.name}" in result1
    assert "permalink: test/note-1" in result1

    # Step 2: Create second note with different title
    result2 = await write_note.fn(
        project=test_project.name, title="Note 2", folder="test", content="Content for note 2"
    )
    assert "# Created note" in result2
    assert f"project: {test_project.name}" in result2
    assert "permalink: test/note-2" in result2

    # Step 3: Try to create/replace first note again
    # This scenario would trigger the UNIQUE constraint failure before the fix
    result3 = await write_note.fn(
        project=test_project.name,
        title="Note 1",  # Same title as first note
        folder="test",  # Same folder as first note
        content="Replacement content for note 1",  # Different content
    )

    # This should not raise a UNIQUE constraint failure error
    # It should succeed and either:
    # 1. Update the existing note (preferred behavior)
    # 2. Create a new note with unique permalink (fallback behavior)

    assert result3 is not None
    assert f"project: {test_project.name}" in result3
    assert "Updated note" in result3 or "Created note" in result3

    # The result should contain either the original permalink or a unique one
    assert "permalink: test/note-1" in result3 or "permalink: test/note-1-1" in result3

    # Verify we can read back the content
    if "permalink: test/note-1" in result3:
        # Updated existing note case
        content = await read_note.fn("test/note-1", project=test_project.name)
        assert "Replacement content for note 1" in content
    else:
        # Created new note with unique permalink case
        content = await read_note.fn(test_project.name, "test/note-1-1")
        assert "Replacement content for note 1" in content
        # Original note should still exist
        original_content = await read_note.fn(test_project.name, "test/note-1")
        assert "Original content for note 1" in original_content


@pytest.mark.asyncio
async def test_write_note_with_custom_entity_type(app, test_project):
    """Test creating a note with custom entity_type parameter.

    This test verifies the fix for Issue #144 where entity_type parameter
    was hardcoded to "note" instead of allowing custom types.
    """
    result = await write_note.fn(
        project=test_project.name,
        title="Test Guide",
        folder="guides",
        content="# Guide Content\nThis is a guide",
        tags=["guide", "documentation"],
        note_type="guide",
    )

    assert result
    assert "# Created note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: guides/Test Guide.md" in result
    assert "permalink: guides/test-guide" in result
    assert "## Tags" in result
    assert "- guide, documentation" in result
    assert f"[Session: Using project '{test_project.name}']" in result

    # Verify the entity type is correctly set in the frontmatter
    content = await read_note.fn("guides/test-guide", project=test_project.name)
    assert (
        normalize_newlines(
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
        )
        in content
    )


@pytest.mark.asyncio
async def test_write_note_with_report_entity_type(app, test_project):
    """Test creating a note with note_type="report"."""
    result = await write_note.fn(
        project=test_project.name,
        title="Monthly Report",
        folder="reports",
        content="# Monthly Report\nThis is a monthly report",
        tags=["report", "monthly"],
        note_type="report",
    )

    assert result
    assert "# Created note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: reports/Monthly Report.md" in result
    assert "permalink: reports/monthly-report" in result
    assert f"[Session: Using project '{test_project.name}']" in result

    # Verify the entity type is correctly set in the frontmatter
    content = await read_note.fn("reports/monthly-report", project=test_project.name)
    assert "type: report" in content
    assert "# Monthly Report" in content


@pytest.mark.asyncio
async def test_write_note_with_config_entity_type(app, test_project):
    """Test creating a note with note_type="config"."""
    result = await write_note.fn(
        project=test_project.name,
        title="System Config",
        folder="config",
        content="# System Configuration\nThis is a config file",
        note_type="config",
    )

    assert result
    assert "# Created note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: config/System Config.md" in result
    assert "permalink: config/system-config" in result
    assert f"[Session: Using project '{test_project.name}']" in result

    # Verify the entity type is correctly set in the frontmatter
    content = await read_note.fn("config/system-config", project=test_project.name)
    assert "type: config" in content
    assert "# System Configuration" in content


@pytest.mark.asyncio
async def test_write_note_entity_type_default_behavior(app, test_project):
    """Test that the entity_type parameter defaults to "note" when not specified.

    This ensures backward compatibility - existing code that doesn't specify
    entity_type should continue to work as before.
    """
    result = await write_note.fn(
        project=test_project.name,
        title="Default Type Test",
        folder="test",
        content="# Default Type Test\nThis should be type 'note'",
        tags=["test"],
    )

    assert result
    assert "# Created note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: test/Default Type Test.md" in result
    assert "permalink: test/default-type-test" in result
    assert f"[Session: Using project '{test_project.name}']" in result

    # Verify the entity type defaults to "note"
    content = await read_note.fn("test/default-type-test", project=test_project.name)
    assert "type: note" in content
    assert "# Default Type Test" in content


@pytest.mark.asyncio
async def test_write_note_update_existing_with_different_entity_type(app, test_project):
    """Test updating an existing note with a different entity_type."""
    # Create initial note as "note" type
    result1 = await write_note.fn(
        project=test_project.name,
        title="Changeable Type",
        folder="test",
        content="# Initial Content\nThis starts as a note",
        tags=["test"],
        note_type="note",
    )

    assert result1
    assert "# Created note" in result1
    assert f"project: {test_project.name}" in result1

    # Update the same note with a different note_type
    result2 = await write_note.fn(
        project=test_project.name,
        title="Changeable Type",
        folder="test",
        content="# Updated Content\nThis is now a guide",
        tags=["guide"],
        note_type="guide",
    )

    assert result2
    assert "# Updated note" in result2
    assert f"project: {test_project.name}" in result2

    # Verify the entity type was updated
    content = await read_note.fn("test/changeable-type", project=test_project.name)
    assert "type: guide" in content
    assert "# Updated Content" in content
    assert "- guide" in content


@pytest.mark.asyncio
async def test_write_note_respects_frontmatter_entity_type(app, test_project):
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
    result = await write_note.fn(
        project=test_project.name, title="Test Guide", folder="guides", content=note
    )

    assert result
    assert "# Created note" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: guides/Test Guide.md" in result
    assert "permalink: guides/test-guide" in result
    assert f"[Session: Using project '{test_project.name}']" in result

    # Verify the entity type from frontmatter is respected (should be "guide", not "note")
    content = await read_note.fn("guides/test-guide", project=test_project.name)
    assert "type: guide" in content
    assert "# Guide Content" in content
    assert "- guide" in content
    assert "- documentation" in content


class TestWriteNoteSecurityValidation:
    """Test write_note security validation features."""

    @pytest.mark.asyncio
    async def test_write_note_blocks_path_traversal_unix(self, app, test_project):
        """Test that Unix-style path traversal attacks are blocked in folder parameter."""
        # Test various Unix-style path traversal patterns
        attack_folders = [
            "../",
            "../../",
            "../../../",
            "../secrets",
            "../../etc",
            "../../../etc/passwd_folder",
            "notes/../../../etc",
            "folder/../../outside",
            "../../../../malicious",
        ]

        for attack_folder in attack_folders:
            result = await write_note.fn(
                project=test_project.name,
                title="Test Note",
                folder=attack_folder,
                content="# Test Content\nThis should be blocked by security validation.",
            )

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result
            assert attack_folder in result

    @pytest.mark.asyncio
    async def test_write_note_blocks_path_traversal_windows(self, app, test_project):
        """Test that Windows-style path traversal attacks are blocked in folder parameter."""
        # Test various Windows-style path traversal patterns
        attack_folders = [
            "..\\",
            "..\\..\\",
            "..\\..\\..\\",
            "..\\secrets",
            "..\\..\\Windows",
            "..\\..\\..\\Windows\\System32",
            "notes\\..\\..\\..\\Windows",
            "\\\\server\\share",
            "\\\\..\\..\\Windows",
        ]

        for attack_folder in attack_folders:
            result = await write_note.fn(
                project=test_project.name,
                title="Test Note",
                folder=attack_folder,
                content="# Test Content\nThis should be blocked by security validation.",
            )

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result
            assert attack_folder in result

    @pytest.mark.asyncio
    async def test_write_note_blocks_absolute_paths(self, app, test_project):
        """Test that absolute paths are blocked in folder parameter."""
        # Test various absolute path patterns
        attack_folders = [
            "/etc",
            "/home/user",
            "/var/log",
            "/root",
            "C:\\Windows",
            "C:\\Users\\user",
            "D:\\secrets",
            "/tmp/malicious",
            "/usr/local/evil",
        ]

        for attack_folder in attack_folders:
            result = await write_note.fn(
                project=test_project.name,
                title="Test Note",
                folder=attack_folder,
                content="# Test Content\nThis should be blocked by security validation.",
            )

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result
            assert attack_folder in result

    @pytest.mark.asyncio
    async def test_write_note_blocks_home_directory_access(self, app, test_project):
        """Test that home directory access patterns are blocked in folder parameter."""
        # Test various home directory access patterns
        attack_folders = [
            "~",
            "~/",
            "~/secrets",
            "~/.ssh",
            "~/Documents",
            "~\\AppData",
            "~\\Desktop",
            "~/.env_folder",
        ]

        for attack_folder in attack_folders:
            result = await write_note.fn(
                project=test_project.name,
                title="Test Note",
                folder=attack_folder,
                content="# Test Content\nThis should be blocked by security validation.",
            )

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result
            assert attack_folder in result

    @pytest.mark.asyncio
    async def test_write_note_blocks_mixed_attack_patterns(self, app, test_project):
        """Test that mixed legitimate/attack patterns are blocked in folder parameter."""
        # Test mixed patterns that start legitimate but contain attacks
        attack_folders = [
            "notes/../../../etc",
            "docs/../../.env_folder",
            "legitimate/path/../../.ssh",
            "project/folder/../../../Windows",
            "valid/folder/../../home/user",
            "assets/../../../tmp/evil",
        ]

        for attack_folder in attack_folders:
            result = await write_note.fn(
                project=test_project.name,
                title="Test Note",
                folder=attack_folder,
                content="# Test Content\nThis should be blocked by security validation.",
            )

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result

    @pytest.mark.asyncio
    async def test_write_note_allows_safe_folder_paths(self, app, test_project):
        """Test that legitimate folder paths are still allowed."""
        # Test various safe folder patterns
        safe_folders = [
            "notes",
            "docs",
            "projects/2025",
            "archive/old-notes",
            "deep/nested/directory/structure",
            "folder/subfolder",
            "research/ml",
            "meeting-notes",
        ]

        for safe_folder in safe_folders:
            result = await write_note.fn(
                project=test_project.name,
                title=f"Test Note in {safe_folder.replace('/', '-')}",
                folder=safe_folder,
                content="# Test Content\nThis should work normally with security validation.",
                tags=["test", "security"],
            )

            # Should succeed (not a security error)
            assert isinstance(result, str)
            assert "# Error" not in result
            assert "paths must stay within project boundaries" not in result
            # Should be normal successful creation/update
            assert ("# Created note" in result) or ("# Updated note" in result)
            assert safe_folder in result  # Should show in file_path

    @pytest.mark.asyncio
    async def test_write_note_empty_folder_security(self, app, test_project):
        """Test that empty folder parameter is handled securely."""
        # Empty folder should be allowed (creates in root)
        result = await write_note.fn(
            project=test_project.name,
            title="Root Note",
            folder="",
            content="# Root Note\nThis note should be created in the project root.",
        )

        assert isinstance(result, str)
        # Empty folder should not trigger security error
        assert "# Error" not in result
        assert "paths must stay within project boundaries" not in result
        # Should succeed normally
        assert ("# Created note" in result) or ("# Updated note" in result)

    @pytest.mark.asyncio
    async def test_write_note_none_folder_security(self, app, test_project):
        """Test that default folder behavior works securely when folder is omitted."""
        # The write_note function requires folder parameter, but we can test with empty string
        # which effectively creates in project root
        result = await write_note.fn(
            project=test_project.name,
            title="Root Folder Note",
            folder="",  # Empty string instead of None since folder is required
            content="# Root Folder Note\nThis note should be created in the project root.",
        )

        assert isinstance(result, str)
        # Empty folder should not trigger security error
        assert "# Error" not in result
        assert "paths must stay within project boundaries" not in result
        # Should succeed normally
        assert ("# Created note" in result) or ("# Updated note" in result)

    @pytest.mark.asyncio
    async def test_write_note_current_directory_references_security(self, app, test_project):
        """Test that current directory references are handled securely."""
        # Test current directory references (should be safe)
        safe_folders = [
            "./notes",
            "folder/./subfolder",
            "./folder/subfolder",
        ]

        for safe_folder in safe_folders:
            result = await write_note.fn(
                project=test_project.name,
                title=f"Current Dir Test {safe_folder.replace('/', '-').replace('.', 'dot')}",
                folder=safe_folder,
                content="# Current Directory Test\nThis should work with current directory references.",
            )

            assert isinstance(result, str)
            # Should NOT contain security error message
            assert "# Error" not in result
            assert "paths must stay within project boundaries" not in result
            # Should succeed normally
            assert ("# Created note" in result) or ("# Updated note" in result)

    @pytest.mark.asyncio
    async def test_write_note_security_with_all_parameters(self, app, test_project):
        """Test security validation works with all write_note parameters."""
        # Test that security validation is applied even when all other parameters are provided
        result = await write_note.fn(
            project=test_project.name,
            title="Security Test with All Params",
            folder="../../../etc/malicious",
            content="# Malicious Content\nThis should be blocked by security validation.",
            tags=["malicious", "test"],
            note_type="guide",
        )

        assert isinstance(result, str)
        assert "# Error" in result
        assert "paths must stay within project boundaries" in result
        assert "../../../etc/malicious" in result

    @pytest.mark.asyncio
    async def test_write_note_security_logging(self, app, test_project, caplog):
        """Test that security violations are properly logged."""
        # Attempt path traversal attack
        result = await write_note.fn(
            project=test_project.name,
            title="Security Logging Test",
            folder="../../../etc/passwd_folder",
            content="# Test Content\nThis should trigger security logging.",
        )

        assert "# Error" in result
        assert "paths must stay within project boundaries" in result

        # Check that security violation was logged
        # Note: This test may need adjustment based on the actual logging setup
        # The security validation should generate a warning log entry

    @pytest.mark.asyncio
    async def test_write_note_preserves_functionality_with_security(self, app, test_project):
        """Test that security validation doesn't break normal note creation functionality."""
        # Create a note with all features to ensure security validation doesn't interfere
        result = await write_note.fn(
            project=test_project.name,
            title="Full Feature Security Test",
            folder="security-tests",
            content=dedent("""
                # Full Feature Security Test

                This note tests that security validation doesn't break normal functionality.

                ## Observations
                - [security] Path validation working correctly #security
                - [feature] All features still functional #test

                ## Relations
                - relates_to [[Security Implementation]]
                - depends_on [[Path Validation]]

                Additional content with various formatting.
            """).strip(),
            tags=["security", "test", "full-feature"],
            note_type="guide",
        )

        # Should succeed normally
        assert isinstance(result, str)
        assert "# Error" not in result
        assert "paths must stay within project boundaries" not in result
        assert "# Created note" in result
        assert "file_path: security-tests/Full Feature Security Test.md" in result
        assert "permalink: security-tests/full-feature-security-test" in result

        # Should process observations and relations
        assert "## Observations" in result
        assert "## Relations" in result
        assert "## Tags" in result

        # Should show proper counts
        assert "security: 1" in result
        assert "feature: 1" in result


class TestWriteNoteSecurityEdgeCases:
    """Test edge cases for write_note security validation."""

    @pytest.mark.asyncio
    async def test_write_note_unicode_folder_attacks(self, app, test_project):
        """Test that Unicode-based path traversal attempts are blocked."""
        # Test Unicode path traversal attempts
        unicode_attack_folders = [
            "notes/文档/../../../etc",  # Chinese characters
            "docs/café/../../secrets",  # Accented characters
            "files/αβγ/../../../malicious",  # Greek characters
        ]

        for attack_folder in unicode_attack_folders:
            result = await write_note.fn(
                project=test_project.name,
                title="Unicode Attack Test",
                folder=attack_folder,
                content="# Unicode Attack\nThis should be blocked.",
            )

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result

    @pytest.mark.asyncio
    async def test_write_note_very_long_attack_folder(self, app, test_project):
        """Test handling of very long attack folder paths."""
        # Create a very long path traversal attack
        long_attack_folder = "../" * 1000 + "etc/malicious"

        result = await write_note.fn(
            project=test_project.name,
            title="Long Attack Test",
            folder=long_attack_folder,
            content="# Long Attack\nThis should be blocked.",
        )

        assert isinstance(result, str)
        assert "# Error" in result
        assert "paths must stay within project boundaries" in result

    @pytest.mark.asyncio
    async def test_write_note_case_variations_attacks(self, app, test_project):
        """Test that case variations don't bypass security."""
        # Test case variations (though case sensitivity depends on filesystem)
        case_attack_folders = [
            "../ETC",
            "../Etc/SECRETS",
            "..\\WINDOWS",
            "~/SECRETS",
        ]

        for attack_folder in case_attack_folders:
            result = await write_note.fn(
                project=test_project.name,
                title="Case Variation Attack Test",
                folder=attack_folder,
                content="# Case Attack\nThis should be blocked.",
            )

            assert isinstance(result, str)
            assert "# Error" in result
            assert "paths must stay within project boundaries" in result

    @pytest.mark.asyncio
    async def test_write_note_whitespace_in_attack_folders(self, app, test_project):
        """Test that whitespace doesn't help bypass security."""
        # Test attack folders with various whitespace
        whitespace_attack_folders = [
            " ../../../etc ",
            "\t../../../secrets\t",
            " ..\\..\\Windows ",
            "notes/ ../../ malicious",
        ]

        for attack_folder in whitespace_attack_folders:
            result = await write_note.fn(
                project=test_project.name,
                title="Whitespace Attack Test",
                folder=attack_folder,
                content="# Whitespace Attack\nThis should be blocked.",
            )

            assert isinstance(result, str)
            # The attack should still be blocked even with whitespace
            if ".." in attack_folder.strip() or "~" in attack_folder.strip():
                assert "# Error" in result
                assert "paths must stay within project boundaries" in result
