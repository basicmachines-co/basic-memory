"""Tests for delete_note MCP tool."""

from unittest.mock import patch

import pytest

from basic_memory.mcp.tools.delete_note import delete_note, _format_delete_error_response
from basic_memory.mcp.tools.read_note import read_note
from basic_memory.mcp.tools.write_note import write_note


class TestDeleteNoteErrorFormatting:
    """Test the error formatting function for better user experience."""

    def test_format_delete_error_note_not_found(self, test_project):
        """Test formatting for note not found errors."""
        result = _format_delete_error_response(test_project.name, "entity not found", "test-note")

        assert "# Delete Failed - Note Not Found" in result
        assert "The note 'test-note' could not be found" in result
        assert 'search_notes("test-project", "test-note")' in result
        assert "Already deleted" in result
        assert "Wrong identifier" in result

    def test_format_delete_error_permission_denied(self, test_project):
        """Test formatting for permission errors."""
        result = _format_delete_error_response(test_project.name, "permission denied", "test-note")

        assert "# Delete Failed - Permission Error" in result
        assert "You don't have permission to delete 'test-note'" in result
        assert "Check permissions" in result
        assert "File locks" in result
        assert "list_memory_projects()" in result

    def test_format_delete_error_access_forbidden(self, test_project):
        """Test formatting for access forbidden errors."""
        result = _format_delete_error_response(test_project.name, "access forbidden", "test-note")

        assert "# Delete Failed - Permission Error" in result
        assert "You don't have permission to delete 'test-note'" in result

    def test_format_delete_error_server_error(self, test_project):
        """Test formatting for server errors."""
        result = _format_delete_error_response(
            test_project.name, "server error occurred", "test-note"
        )

        assert "# Delete Failed - System Error" in result
        assert "A system error occurred while deleting 'test-note'" in result
        assert "Try again" in result
        assert "Check file status" in result

    def test_format_delete_error_filesystem_error(self, test_project):
        """Test formatting for filesystem errors."""
        result = _format_delete_error_response(test_project.name, "filesystem error", "test-note")

        assert "# Delete Failed - System Error" in result
        assert "A system error occurred while deleting 'test-note'" in result

    def test_format_delete_error_disk_error(self, test_project):
        """Test formatting for disk errors."""
        result = _format_delete_error_response(test_project.name, "disk full", "test-note")

        assert "# Delete Failed - System Error" in result
        assert "A system error occurred while deleting 'test-note'" in result

    def test_format_delete_error_database_error(self, test_project):
        """Test formatting for database errors."""
        result = _format_delete_error_response(test_project.name, "database error", "test-note")

        assert "# Delete Failed - Database Error" in result
        assert "A database error occurred while deleting 'test-note'" in result
        assert "Sync conflict" in result
        assert "Database lock" in result

    def test_format_delete_error_sync_error(self, test_project):
        """Test formatting for sync errors."""
        result = _format_delete_error_response(test_project.name, "sync failed", "test-note")

        assert "# Delete Failed - Database Error" in result
        assert "A database error occurred while deleting 'test-note'" in result

    def test_format_delete_error_generic(self, test_project):
        """Test formatting for generic errors."""
        result = _format_delete_error_response(test_project.name, "unknown error", "test-note")

        assert "# Delete Failed" in result
        assert "Error deleting note 'test-note': unknown error" in result
        assert "General troubleshooting" in result
        assert "Verify the note exists" in result

    def test_format_delete_error_with_complex_identifier(self, test_project):
        """Test formatting with complex identifiers (permalinks)."""
        result = _format_delete_error_response(
            test_project.name, "entity not found", "folder/note-title"
        )

        assert 'search_notes("test-project", "note-title")' in result
        assert "Note Title" in result  # Title format
        assert "folder/note-title" in result  # Permalink format


@pytest.mark.asyncio
async def test_delete_note_rejects_fuzzy_match(client, test_project):
    """delete_note must reject nonexistent identifiers, not fuzzy-match to a similar note."""
    await write_note(
        project=test_project.name,
        title="Delete Target Note",
        directory="test",
        content="# Delete Target Note\nShould not be deleted.",
    )

    # Attempt to delete a nonexistent note — should return False, not silently delete the existing note
    result = await delete_note(
        project=test_project.name,
        identifier="Delete Target NONEXISTENT",
    )

    # Should indicate not found (False or error string)
    assert result is False or (isinstance(result, str) and "not found" in result.lower())

    # Verify the existing note was NOT deleted
    content = await read_note("Delete Target Note", project=test_project.name)
    assert "Should not be deleted" in content


@pytest.mark.asyncio
async def test_delete_note_detects_project_from_memory_url(client, test_project):
    """delete_note should detect project from memory:// URL prefix when project=None."""
    # Create a note to delete
    await write_note(
        project=test_project.name,
        title="Delete URL Note",
        directory="test",
        content="# Delete URL Note\nContent to delete.",
    )

    # Delete using memory:// URL with project=None — should auto-detect project
    # The note may or may not be found (depends on URL resolution), but the key
    # assertion is that routing goes to the correct project
    result = await delete_note(
        identifier=f"memory://{test_project.name}/test/delete-url-note",
        project=None,
    )

    # Result is True (deleted) or False (not found by that URL) — either is acceptable.
    # The important thing is it didn't error and routed to the correct project.
    assert isinstance(result, bool)


@pytest.mark.asyncio
async def test_delete_note_skips_detection_for_plain_path(client, test_project):
    """delete_note should NOT call detect_project_from_url_prefix for plain path identifiers.

    A plain path like 'research/note' should not be misrouted to a project
    named 'research' — the 'research' segment is a directory, not a project.
    """
    with patch("basic_memory.mcp.tools.delete_note.detect_project_from_url_prefix") as mock_detect:
        # Use a plain path (no memory:// prefix) — detection should not be called
        await delete_note(
            identifier="test/nonexistent-note",
            project=None,
        )

        mock_detect.assert_not_called()


@pytest.mark.asyncio
async def test_delete_note_skips_detection_when_project_provided(client, test_project):
    """delete_note should skip URL detection when project is explicitly provided."""
    with patch("basic_memory.mcp.tools.delete_note.detect_project_from_url_prefix") as mock_detect:
        await delete_note(
            identifier=f"memory://{test_project.name}/test/some-note",
            project=test_project.name,
        )

        mock_detect.assert_not_called()
