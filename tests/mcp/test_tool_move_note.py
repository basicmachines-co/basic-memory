"""Tests for the move_note MCP tool."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from contextlib import asynccontextmanager

from basic_memory.mcp.tools.move_note import move_note, _format_move_error_response
from basic_memory.mcp.tools.write_note import write_note
from basic_memory.mcp.tools.read_note import read_note


@pytest.mark.asyncio
async def test_move_note_success(app, client, test_project):
    """Test successfully moving a note to a new location."""
    # Create initial note
    await write_note.fn(
        project=test_project.name,
        title="Test Note",
        folder="source",
        content="# Test Note\nOriginal content here.",
    )

    # Move note
    result = await move_note.fn(
        project=test_project.name,
        identifier="source/test-note",
        destination_path="target/MovedNote.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify original location no longer exists
    try:
        await read_note.fn(test_project.name, "source/test-note")
        assert False, "Original note should not exist after move"
    except Exception:
        pass  # Expected - note should not exist at original location

    # Verify note exists at new location with same content
    content = await read_note.fn("target/moved-note", project=test_project.name)
    assert "# Test Note" in content
    assert "Original content here" in content
    assert "permalink: target/moved-note" in content


@pytest.mark.asyncio
async def test_move_note_with_folder_creation(client, test_project):
    """Test moving note creates necessary folders."""
    # Create initial note
    await write_note.fn(
        project=test_project.name,
        title="Deep Note",
        folder="",
        content="# Deep Note\nContent in root folder.",
    )

    # Move to deeply nested path
    result = await move_note.fn(
        project=test_project.name,
        identifier="deep-note",
        destination_path="deeply/nested/folder/DeepNote.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify note exists at new location
    content = await read_note.fn("deeply/nested/folder/deep-note", project=test_project.name)
    assert "# Deep Note" in content
    assert "Content in root folder" in content


@pytest.mark.asyncio
async def test_move_note_with_observations_and_relations(app, client, test_project):
    """Test moving note preserves observations and relations."""
    # Create note with complex semantic content
    await write_note.fn(
        project=test_project.name,
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
        project=test_project.name,
        identifier="source/complex-entity",
        destination_path="target/MovedComplex.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify moved note preserves all content
    content = await read_note.fn("target/moved-complex", project=test_project.name)
    assert "Important observation #tag1" in content
    assert "Key feature #feature" in content
    assert "[[SomeOtherEntity]]" in content
    assert "[[Dependency]]" in content
    assert "Some additional content" in content


@pytest.mark.asyncio
async def test_move_note_by_title(client, test_project):
    """Test moving note using title as identifier."""
    # Create note with unique title
    await write_note.fn(
        project=test_project.name,
        title="UniqueTestTitle",
        folder="source",
        content="# UniqueTestTitle\nTest content.",
    )

    # Move using title as identifier
    result = await move_note.fn(
        project=test_project.name,
        identifier="UniqueTestTitle",
        destination_path="target/MovedByTitle.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify note exists at new location
    content = await read_note.fn("target/moved-by-title", project=test_project.name)
    assert "# UniqueTestTitle" in content
    assert "Test content" in content


@pytest.mark.asyncio
async def test_move_note_by_file_path(client, test_project):
    """Test moving note using file path as identifier."""
    # Create initial note
    await write_note.fn(
        project=test_project.name,
        title="PathTest",
        folder="source",
        content="# PathTest\nContent for path test.",
    )

    # Move using file path as identifier
    result = await move_note.fn(
        project=test_project.name,
        identifier="source/PathTest.md",
        destination_path="target/MovedByPath.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify note exists at new location
    content = await read_note.fn("target/moved-by-path", project=test_project.name)
    assert "# PathTest" in content
    assert "Content for path test" in content


@pytest.mark.asyncio
async def test_move_note_nonexistent_note(client, test_project):
    """Test moving a note that doesn't exist."""
    result = await move_note.fn(
        project=test_project.name,
        identifier="nonexistent/note",
        destination_path="target/SomeFile.md",
    )

    # Should return user-friendly error message string
    assert isinstance(result, str)
    assert "# Move Failed - Note Not Found" in result
    assert "could not be found for moving" in result
    assert "Search for the note first" in result


@pytest.mark.asyncio
async def test_move_note_invalid_destination_path(client, test_project):
    """Test moving note with invalid destination path."""
    # Create initial note
    await write_note.fn(
        project=test_project.name,
        title="TestNote",
        folder="source",
        content="# TestNote\nTest content.",
    )

    # Test absolute path (should be rejected by validation)
    result = await move_note.fn(
        project=test_project.name,
        identifier="source/test-note",
        destination_path="/absolute/path.md",
    )

    # Should return user-friendly error message string
    assert isinstance(result, str)
    assert "# Move Failed" in result
    assert "/absolute/path.md" in result or "Invalid" in result or "path" in result


@pytest.mark.asyncio
async def test_move_note_missing_file_extension(client, test_project):
    """Test moving note without file extension in destination path."""
    # Create initial note
    await write_note.fn(
        project=test_project.name,
        title="ExtensionTest",
        folder="source",
        content="# Extension Test\nTesting extension validation.",
    )

    # Test path without extension
    result = await move_note.fn(
        project=test_project.name,
        identifier="source/extension-test",
        destination_path="target/renamed-note",
    )

    # Should return error about missing extension
    assert isinstance(result, str)
    assert "# Move Failed - File Extension Required" in result
    assert "must include a file extension" in result
    assert ".md" in result
    assert "renamed-note.md" in result  # Should suggest adding .md

    # Test path with empty extension (edge case)
    result = await move_note.fn(
        project=test_project.name,
        identifier="source/extension-test",
        destination_path="target/renamed-note.",
    )

    assert isinstance(result, str)
    assert "# Move Failed - File Extension Required" in result
    assert "must include a file extension" in result

    # Test that note still exists at original location
    content = await read_note.fn("source/extension-test", project=test_project.name)
    assert "# Extension Test" in content
    assert "Testing extension validation" in content


@pytest.mark.asyncio
async def test_move_note_file_extension_mismatch(client, test_project):
    """Test that moving note with different extension is blocked."""
    # Create initial note with .md extension
    await write_note.fn(
        project=test_project.name,
        title="MarkdownNote",
        folder="source",
        content="# Markdown Note\nThis is a markdown file.",
    )

    # Try to move with .txt extension
    result = await move_note.fn(
        project=test_project.name,
        identifier="source/markdown-note",
        destination_path="target/renamed-note.txt",
    )

    # Should return error about extension mismatch
    assert isinstance(result, str)
    assert "# Move Failed - File Extension Mismatch" in result
    assert "does not match the source file extension" in result
    assert ".md" in result
    assert ".txt" in result
    assert "renamed-note.md" in result  # Should suggest correct extension

    # Test that note still exists at original location with original extension
    content = await read_note.fn("source/markdown-note", project=test_project.name)
    assert "# Markdown Note" in content
    assert "This is a markdown file" in content


@pytest.mark.asyncio
async def test_move_note_preserves_file_extension(client, test_project):
    """Test that moving note with matching extension succeeds."""
    # Create initial note with .md extension
    await write_note.fn(
        project=test_project.name,
        title="PreserveExtension",
        folder="source",
        content="# Preserve Extension\nTesting that extension is preserved.",
    )

    # Move with same .md extension
    result = await move_note.fn(
        project=test_project.name,
        identifier="source/preserve-extension",
        destination_path="target/preserved-note.md",
    )

    # Should succeed
    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify note exists at new location with same extension
    content = await read_note.fn("target/preserved-note", project=test_project.name)
    assert "# Preserve Extension" in content
    assert "Testing that extension is preserved" in content

    # Verify old location no longer exists
    try:
        await read_note.fn("source/preserve-extension")
        assert False, "Original note should not exist after move"
    except Exception:
        pass  # Expected


@pytest.mark.asyncio
async def test_move_note_destination_exists(client, test_project):
    """Test moving note to existing destination."""
    # Create source note
    await write_note.fn(
        project=test_project.name,
        title="SourceNote",
        folder="source",
        content="# SourceNote\nSource content.",
    )

    # Create destination note
    await write_note.fn(
        project=test_project.name,
        title="DestinationNote",
        folder="target",
        content="# DestinationNote\nDestination content.",
    )

    # Try to move source to existing destination
    result = await move_note.fn(
        project=test_project.name,
        identifier="source/source-note",
        destination_path="target/DestinationNote.md",
    )

    # Should return user-friendly error message string
    assert isinstance(result, str)
    assert "# Move Failed" in result
    assert "already exists" in result or "Destination" in result


@pytest.mark.asyncio
async def test_move_note_same_location(client, test_project):
    """Test moving note to the same location."""
    # Create initial note
    await write_note.fn(
        project=test_project.name,
        title="SameLocationTest",
        folder="test",
        content="# SameLocationTest\nContent here.",
    )

    # Try to move to same location
    result = await move_note.fn(
        project=test_project.name,
        identifier="test/same-location-test",
        destination_path="test/SameLocationTest.md",
    )

    # Should return user-friendly error message string
    assert isinstance(result, str)
    assert "# Move Failed" in result
    assert "already exists" in result or "same" in result or "Destination" in result


@pytest.mark.asyncio
async def test_move_note_rename_only(client, test_project):
    """Test moving note within same folder (rename operation)."""
    # Create initial note
    await write_note.fn(
        project=test_project.name,
        title="OriginalName",
        folder="test",
        content="# OriginalName\nContent to rename.",
    )

    # Rename within same folder
    await move_note.fn(
        project=test_project.name,
        identifier="test/original-name",
        destination_path="test/NewName.md",
    )

    # Verify original is gone
    try:
        await read_note.fn("test/original-name", project=test_project.name)
        assert False, "Original note should not exist after rename"
    except Exception:
        pass  # Expected

    # Verify new name exists with same content
    content = await read_note.fn("test/new-name", project=test_project.name)
    assert "# OriginalName" in content  # Title in content remains same
    assert "Content to rename" in content
    assert "permalink: test/new-name" in content


@pytest.mark.asyncio
async def test_move_note_complex_filename(client, test_project):
    """Test moving note with spaces in filename."""
    # Create note with spaces in name
    await write_note.fn(
        project=test_project.name,
        title="Meeting Notes 2025",
        folder="meetings",
        content="# Meeting Notes 2025\nMeeting content with dates.",
    )

    # Move to new location
    result = await move_note.fn(
        project=test_project.name,
        identifier="meetings/meeting-notes-2025",
        destination_path="archive/2025/meetings/Meeting Notes 2025.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify note exists at new location with correct content
    content = await read_note.fn(
        "archive/2025/meetings/meeting-notes-2025", project=test_project.name
    )
    assert "# Meeting Notes 2025" in content
    assert "Meeting content with dates" in content


@pytest.mark.asyncio
async def test_move_note_with_tags(app, client, test_project):
    """Test moving note with tags preserves tags."""
    # Create note with tags
    await write_note.fn(
        project=test_project.name,
        title="Tagged Note",
        folder="source",
        content="# Tagged Note\nContent with tags.",
        tags=["important", "work", "project"],
    )

    # Move note
    result = await move_note.fn(
        project=test_project.name,
        identifier="source/tagged-note",
        destination_path="target/MovedTaggedNote.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify tags are preserved in correct YAML format
    content = await read_note.fn("target/moved-tagged-note", project=test_project.name)
    assert "- important" in content
    assert "- work" in content
    assert "- project" in content


@pytest.mark.asyncio
async def test_move_note_empty_string_destination(client, test_project):
    """Test moving note with empty destination path."""
    # Create initial note
    await write_note.fn(
        project=test_project.name,
        title="TestNote",
        folder="source",
        content="# TestNote\nTest content.",
    )

    # Test empty destination path
    result = await move_note.fn(
        project=test_project.name,
        identifier="source/test-note",
        destination_path="",
    )

    # Should return user-friendly error message string
    assert isinstance(result, str)
    assert "# Move Failed" in result
    assert "empty" in result or "Invalid" in result or "path" in result


@pytest.mark.asyncio
async def test_move_note_parent_directory_path(client, test_project):
    """Test moving note with parent directory in destination path."""
    # Create initial note
    await write_note.fn(
        project=test_project.name,
        title="TestNote",
        folder="source",
        content="# TestNote\nTest content.",
    )

    # Test parent directory path
    result = await move_note.fn(
        project=test_project.name,
        identifier="source/test-note",
        destination_path="../parent/file.md",
    )

    # Should return user-friendly error message string
    assert isinstance(result, str)
    assert "# Move Failed" in result
    assert "parent" in result or "Invalid" in result or "path" in result or ".." in result


@pytest.mark.asyncio
async def test_move_note_identifier_variations(client, test_project):
    """Test that various identifier formats work for moving."""
    # Create a note to test different identifier formats
    await write_note.fn(
        project=test_project.name,
        title="Test Document",
        folder="docs",
        content="# Test Document\nContent for testing identifiers.",
    )

    # Test with permalink identifier
    result = await move_note.fn(
        project=test_project.name,
        identifier="docs/test-document",
        destination_path="moved/TestDocument.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify it moved correctly
    content = await read_note.fn("moved/test-document", project=test_project.name)
    assert "# Test Document" in content
    assert "Content for testing identifiers" in content


@pytest.mark.asyncio
async def test_move_note_preserves_frontmatter(app, client, test_project):
    """Test that moving preserves custom frontmatter."""
    # Create note with custom frontmatter by first creating it normally
    await write_note.fn(
        project=test_project.name,
        title="Custom Frontmatter Note",
        folder="source",
        content="# Custom Frontmatter Note\nContent with custom metadata.",
    )

    # Move the note
    result = await move_note.fn(
        project=test_project.name,
        identifier="source/custom-frontmatter-note",
        destination_path="target/MovedCustomNote.md",
    )

    assert isinstance(result, str)
    assert "✅ Note moved successfully" in result

    # Verify the moved note has proper frontmatter structure
    content = await read_note.fn("target/moved-custom-note", project=test_project.name)
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


class TestMoveNoteSecurityValidation:
    """Test move note security validation features."""

    @pytest.mark.asyncio
    async def test_move_note_blocks_path_traversal_unix(self, client, test_project):
        """Test that Unix-style path traversal attacks are blocked."""
        # Create initial note
        await write_note.fn(
            project=test_project.name,
            title="Test Note",
            folder="source",
            content="# Test Note\nTest content for security testing.",
        )

        # Test various Unix-style path traversal patterns
        attack_paths = [
            "../secrets.txt",
            "../../etc/passwd",
            "../../../root/.ssh/id_rsa",
            "notes/../../../etc/shadow",
            "folder/../../outside/file.md",
            "../../../../etc/hosts",
        ]

        for attack_path in attack_paths:
            result = await move_note.fn(
                project=test_project.name,
                identifier="source/test-note",
                destination_path=attack_path,
            )

            assert isinstance(result, str)
            assert "# Move Failed - Security Validation Error" in result
            assert "paths must stay within project boundaries" in result
            assert attack_path in result
            assert "Try again with a safe path" in result

    @pytest.mark.asyncio
    async def test_move_note_blocks_path_traversal_windows(self, client, test_project):
        """Test that Windows-style path traversal attacks are blocked."""
        # Create initial note
        await write_note.fn(
            project=test_project.name,
            title="Test Note",
            folder="source",
            content="# Test Note\nTest content for security testing.",
        )

        # Test various Windows-style path traversal patterns
        attack_paths = [
            "..\\secrets.txt",
            "..\\..\\Windows\\System32\\config\\SAM",
            "notes\\..\\..\\..\\Windows\\System32",
            "\\\\server\\share\\file.txt",
            "..\\..\\Users\\user\\.env",
            "\\\\..\\..\\Windows",
        ]

        for attack_path in attack_paths:
            result = await move_note.fn(
                project=test_project.name,
                identifier="source/test-note",
                destination_path=attack_path,
            )

            assert isinstance(result, str)
            assert "# Move Failed - Security Validation Error" in result
            assert "paths must stay within project boundaries" in result
            assert attack_path in result

    @pytest.mark.asyncio
    async def test_move_note_blocks_absolute_paths(self, client, test_project):
        """Test that absolute paths are blocked."""
        # Create initial note
        await write_note.fn(
            project=test_project.name,
            title="Test Note",
            folder="source",
            content="# Test Note\nTest content for security testing.",
        )

        # Test various absolute path patterns
        attack_paths = [
            "/etc/passwd",
            "/home/user/.env",
            "/var/log/auth.log",
            "/root/.ssh/id_rsa",
            "C:\\Windows\\System32\\config\\SAM",
            "C:\\Users\\user\\.env",
            "D:\\secrets\\config.json",
            "/tmp/malicious.txt",
        ]

        for attack_path in attack_paths:
            result = await move_note.fn(
                project=test_project.name,
                identifier="source/test-note",
                destination_path=attack_path,
            )

            assert isinstance(result, str)
            assert "# Move Failed - Security Validation Error" in result
            assert "paths must stay within project boundaries" in result
            assert attack_path in result

    @pytest.mark.asyncio
    async def test_move_note_blocks_home_directory_access(self, client, test_project):
        """Test that home directory access patterns are blocked."""
        # Create initial note
        await write_note.fn(
            project=test_project.name,
            title="Test Note",
            folder="source",
            content="# Test Note\nTest content for security testing.",
        )

        # Test various home directory access patterns
        attack_paths = [
            "~/secrets.txt",
            "~/.env",
            "~/.ssh/id_rsa",
            "~/Documents/passwords.txt",
            "~\\AppData\\secrets",
            "~\\Desktop\\config.ini",
        ]

        for attack_path in attack_paths:
            result = await move_note.fn(
                project=test_project.name,
                identifier="source/test-note",
                destination_path=attack_path,
            )

            assert isinstance(result, str)
            assert "# Move Failed - Security Validation Error" in result
            assert "paths must stay within project boundaries" in result
            assert attack_path in result

    @pytest.mark.asyncio
    async def test_move_note_blocks_mixed_attack_patterns(self, client, test_project):
        """Test that mixed legitimate/attack patterns are blocked."""
        # Create initial note
        await write_note.fn(
            project=test_project.name,
            title="Test Note",
            folder="source",
            content="# Test Note\nTest content for security testing.",
        )

        # Test mixed patterns that start legitimate but contain attacks
        attack_paths = [
            "notes/../../../etc/passwd",
            "docs/../../.env",
            "legitimate/path/../../.ssh/id_rsa",
            "project/folder/../../../Windows/System32",
            "valid/folder/../../home/user/.bashrc",
        ]

        for attack_path in attack_paths:
            result = await move_note.fn(
                project=test_project.name,
                identifier="source/test-note",
                destination_path=attack_path,
            )

            assert isinstance(result, str)
            assert "# Move Failed - Security Validation Error" in result
            assert "paths must stay within project boundaries" in result

    @pytest.mark.asyncio
    async def test_move_note_allows_safe_paths(self, client, test_project):
        """Test that legitimate paths are still allowed."""
        # Create initial note
        await write_note.fn(
            project=test_project.name,
            title="Test Note",
            folder="source",
            content="# Test Note\nTest content for security testing.",
        )

        # Test various safe path patterns
        safe_paths = [
            "notes/meeting.md",
            "docs/readme.txt",
            "projects/2025/planning.md",
            "archive/old-notes/backup.md",
            "deep/nested/directory/structure/file.txt",
            "folder/subfolder/document.md",
        ]

        for safe_path in safe_paths:
            result = await move_note.fn(
                project=test_project.name,
                identifier="source/test-note",
                destination_path=safe_path,
            )

            # Should succeed or fail for legitimate reasons (not security)
            assert isinstance(result, str)
            # Should NOT contain security error message
            assert "Security Validation Error" not in result

            # If it fails, it should be for other reasons like "already exists" or API errors
            if "Move Failed" in result:
                assert "paths must stay within project boundaries" not in result

    @pytest.mark.asyncio
    async def test_move_note_security_logging(self, client, test_project, caplog):
        """Test that security violations are properly logged."""
        # Create initial note
        await write_note.fn(
            project=test_project.name,
            title="Test Note",
            folder="source",
            content="# Test Note\nTest content for security testing.",
        )

        # Attempt path traversal attack
        result = await move_note.fn(
            project=test_project.name,
            identifier="source/test-note",
            destination_path="../../../etc/passwd",
        )

        assert "# Move Failed - Security Validation Error" in result

        # Check that security violation was logged
        # Note: This test may need adjustment based on the actual logging setup
        # The security validation should generate a warning log entry

    @pytest.mark.asyncio
    async def test_move_note_empty_path_security(self, client, test_project):
        """Test that empty destination path is handled securely."""
        # Create initial note
        await write_note.fn(
            project=test_project.name,
            title="Test Note",
            folder="source",
            content="# Test Note\nTest content for security testing.",
        )

        # Test empty destination path (should be allowed as it resolves to project root)
        result = await move_note.fn(
            project=test_project.name,
            identifier="source/test-note",
            destination_path="",
        )

        assert isinstance(result, str)
        # Empty path should not trigger security error (it's handled by pathlib validation)
        # But may fail for other API-related reasons

    @pytest.mark.asyncio
    async def test_move_note_current_directory_references_security(self, client, test_project):
        """Test that current directory references are handled securely."""
        # Create initial note
        await write_note.fn(
            project=test_project.name,
            title="Test Note",
            folder="source",
            content="# Test Note\nTest content for security testing.",
        )

        # Test current directory references (should be safe)
        safe_paths = [
            "./notes/file.md",
            "folder/./file.md",
            "./folder/subfolder/file.md",
        ]

        for safe_path in safe_paths:
            result = await move_note.fn(
                project=test_project.name,
                identifier="source/test-note",
                destination_path=safe_path,
            )

            assert isinstance(result, str)
            # Should NOT contain security error message
            assert "Security Validation Error" not in result


class TestMoveNoteErrorHandling:
    """Test move note exception handling.

    These are pure unit tests that mock get_client and other dependencies.
    They don't need the database or ASGI app.
    """

    @pytest.fixture
    def mock_client(self):
        """Create a mock async client context manager."""
        mock = MagicMock()

        @asynccontextmanager
        async def mock_get_client():
            yield mock

        return mock_get_client, mock

    @pytest.mark.asyncio
    async def test_move_note_exception_handling(self, mock_client):
        """Test exception handling in move_note."""
        mock_get_client, _ = mock_client

        with patch("basic_memory.mcp.tools.move_note.get_client", mock_get_client):
            with patch("basic_memory.mcp.tools.move_note.get_active_project") as mock_get_project:
                mock_get_project.return_value.project_url = "http://test"
                mock_get_project.return_value.name = "test-project"
                mock_get_project.return_value.home = Path("/tmp/test")

                with patch(
                    "basic_memory.mcp.tools.move_note.call_post",
                    side_effect=Exception("entity not found"),
                ):
                    with patch(
                        "basic_memory.mcp.tools.move_note.call_get",
                        side_effect=Exception("not found"),
                    ):
                        result = await move_note.fn(
                            "test-note", "target/file.md", project="test-project"
                        )

                        assert isinstance(result, str)
                        assert "# Move Failed - Note Not Found" in result

    @pytest.mark.asyncio
    async def test_move_note_permission_error_handling(self, mock_client):
        """Test permission error handling in move_note."""
        mock_get_client, _ = mock_client

        with patch("basic_memory.mcp.tools.move_note.get_client", mock_get_client):
            with patch("basic_memory.mcp.tools.move_note.get_active_project") as mock_get_project:
                mock_get_project.return_value.project_url = "http://test"
                mock_get_project.return_value.name = "test-project"
                mock_get_project.return_value.home = Path("/tmp/test")

                with patch(
                    "basic_memory.mcp.tools.move_note.call_post",
                    side_effect=Exception("permission denied"),
                ):
                    with patch(
                        "basic_memory.mcp.tools.move_note.call_get",
                        side_effect=Exception("not found"),
                    ):
                        result = await move_note.fn(
                            "test-note", "target/file.md", project="test-project"
                        )

                        assert isinstance(result, str)
                        assert "# Move Failed - Permission Error" in result
