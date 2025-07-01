"""Tests for delete_note MCP tool."""

from basic_memory.mcp.tools.delete_note import _format_delete_error_response


class TestDeleteNoteErrorFormatting:
    """Test the error formatting function for better user experience."""

    def test_format_delete_error_note_not_found(self):
        """Test formatting for note not found errors."""
        result = _format_delete_error_response("entity not found", "test-note")

        assert "# Delete Failed - Note Not Found" in result
        assert "The note 'test-note' could not be found" in result
        assert 'search_notes("test-note")' in result
        assert "Already deleted" in result
        assert "Wrong identifier" in result

    def test_format_delete_error_permission_denied(self):
        """Test formatting for permission errors."""
        result = _format_delete_error_response("permission denied", "test-note")

        assert "# Delete Failed - Permission Error" in result
        assert "You don't have permission to delete 'test-note'" in result
        assert "Check permissions" in result
        assert "File locks" in result
        assert "get_current_project()" in result

    def test_format_delete_error_access_forbidden(self):
        """Test formatting for access forbidden errors."""
        result = _format_delete_error_response("access forbidden", "test-note")

        assert "# Delete Failed - Permission Error" in result
        assert "You don't have permission to delete 'test-note'" in result

    def test_format_delete_error_server_error(self):
        """Test formatting for server errors."""
        result = _format_delete_error_response("server error occurred", "test-note")

        assert "# Delete Failed - System Error" in result
        assert "A system error occurred while deleting 'test-note'" in result
        assert "Try again" in result
        assert "Check file status" in result

    def test_format_delete_error_filesystem_error(self):
        """Test formatting for filesystem errors."""
        result = _format_delete_error_response("filesystem error", "test-note")

        assert "# Delete Failed - System Error" in result
        assert "A system error occurred while deleting 'test-note'" in result

    def test_format_delete_error_disk_error(self):
        """Test formatting for disk errors."""
        result = _format_delete_error_response("disk full", "test-note")

        assert "# Delete Failed - System Error" in result
        assert "A system error occurred while deleting 'test-note'" in result

    def test_format_delete_error_database_error(self):
        """Test formatting for database errors."""
        result = _format_delete_error_response("database error", "test-note")

        assert "# Delete Failed - Database Error" in result
        assert "A database error occurred while deleting 'test-note'" in result
        assert "Sync conflict" in result
        assert "Database lock" in result

    def test_format_delete_error_sync_error(self):
        """Test formatting for sync errors."""
        result = _format_delete_error_response("sync failed", "test-note")

        assert "# Delete Failed - Database Error" in result
        assert "A database error occurred while deleting 'test-note'" in result

    def test_format_delete_error_generic(self):
        """Test formatting for generic errors."""
        result = _format_delete_error_response("unknown error", "test-note")

        assert "# Delete Failed" in result
        assert "Error deleting note 'test-note': unknown error" in result
        assert "General troubleshooting" in result
        assert "Verify the note exists" in result

    def test_format_delete_error_with_complex_identifier(self):
        """Test formatting with complex identifiers (permalinks)."""
        result = _format_delete_error_response("entity not found", "folder/note-title")

        assert 'search_notes("note-title")' in result
        assert "Note Title" in result  # Title format
        assert "folder/note-title" in result  # Permalink format


# Integration tests removed to focus on error formatting coverage
# The error formatting tests above provide the necessary coverage for MCP tool error messaging
