"""Tests for CLI --format json functionality.

These tests verify that the --format json flag works correctly for
write-note, read-note, recent-activity, and build-context commands.

See issue #553 for context on these improvements.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


class TestWriteNoteJsonFormat:
    """Tests for write-note --format json."""

    @patch("basic_memory.cli.commands.tool._write_note_json")
    @patch("basic_memory.cli.commands.tool.ConfigManager")
    def test_write_note_json_format(self, mock_config_manager, mock_write_json):
        """Test write-note with --format json returns structured data."""
        # Mock config
        mock_config = MagicMock()
        mock_config.default_project = "test-project"
        mock_config_manager.return_value = mock_config
        mock_config_manager.return_value.get_project.return_value = ("test-project", "/tmp/test")

        # Mock the JSON helper to return entity data
        mock_entity_data = {
            "id": "123",
            "title": "Test Note",
            "permalink": "test-note",
            "file_path": "/tmp/test/test-note.md",
            "checksum": "abc123",
        }
        mock_write_json.return_value = mock_entity_data

        # Run command
        result = runner.invoke(
            cli_app,
            [
                "tool",
                "write-note",
                "--title",
                "Test Note",
                "--folder",
                "notes",
                "--content",
                "Test content",
                "--format",
                "json",
            ],
        )

        # Verify success
        assert result.exit_code == 0

        # Verify JSON output
        output = json.loads(result.output)
        assert output["title"] == "Test Note"
        assert output["permalink"] == "test-note"
        assert "checksum" in output

        # Verify the JSON helper was called (not the MCP tool)
        mock_write_json.assert_called_once()


class TestReadNoteJsonFormat:
    """Tests for read-note --format json."""

    @patch("basic_memory.cli.commands.tool._read_note_json")
    @patch("basic_memory.cli.commands.tool.ConfigManager")
    def test_read_note_json_format(self, mock_config_manager, mock_read_json):
        """Test read-note with --format json returns structured data."""
        # Mock config
        mock_config = MagicMock()
        mock_config.default_project = "test-project"
        mock_config_manager.return_value = mock_config
        mock_config_manager.return_value.get_project.return_value = ("test-project", "/tmp/test")

        # Mock the JSON helper to return entity data
        mock_entity_data = {
            "id": "123",
            "title": "Test Note",
            "permalink": "test-note",
            "content": "Test content",
        }
        mock_read_json.return_value = mock_entity_data

        # Run command
        result = runner.invoke(
            cli_app,
            ["tool", "read-note", "test-note", "--format", "json"],
        )

        # Verify success
        assert result.exit_code == 0

        # Verify JSON output
        output = json.loads(result.output)
        assert output["title"] == "Test Note"
        assert output["content"] == "Test content"

        # Verify the JSON helper was called
        mock_read_json.assert_called_once()

    @patch("basic_memory.cli.commands.tool._read_note_json")
    @patch("basic_memory.cli.commands.tool.ConfigManager")
    def test_read_note_handles_plain_title(self, mock_config_manager, mock_read_json):
        """Test read-note with plain title (not permalink) works with JSON format."""
        # Mock config
        mock_config = MagicMock()
        mock_config.default_project = "test-project"
        mock_config_manager.return_value = mock_config
        mock_config_manager.return_value.get_project.return_value = ("test-project", "/tmp/test")

        # Mock the JSON helper
        mock_entity_data = {"title": "My Note", "permalink": "my-note"}
        mock_read_json.return_value = mock_entity_data

        # Run with plain title (should be handled by memory_url_path in helper)
        result = runner.invoke(
            cli_app,
            ["tool", "read-note", "My Note", "--format", "json"],
        )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["title"] == "My Note"


class TestRecentActivityJsonFormat:
    """Tests for recent-activity --format json."""

    @patch("basic_memory.cli.commands.tool._recent_activity_json")
    @patch("basic_memory.cli.commands.tool.ConfigManager")
    def test_recent_activity_json_format(self, mock_config_manager, mock_activity_json):
        """Test recent-activity with --format json returns structured data."""
        # Mock config
        mock_config = MagicMock()
        mock_config.default_project = "test-project"
        mock_config_manager.return_value = mock_config
        mock_config_manager.return_value.get_project.return_value = ("test-project", "/tmp/test")

        # Mock the JSON helper to return activity data
        mock_activity_data = {
            "results": [
                {"primary_result": {"title": "Note 1", "type": "entity"}},
                {"primary_result": {"title": "Note 2", "type": "entity"}},
            ],
            "metadata": {"total_results": 2},
        }
        mock_activity_json.return_value = mock_activity_data

        # Run command
        result = runner.invoke(
            cli_app,
            ["tool", "recent-activity", "--format", "json"],
        )

        # Verify success
        assert result.exit_code == 0

        # Verify JSON output
        output = json.loads(result.output)
        assert len(output["results"]) == 2
        assert output["metadata"]["total_results"] == 2

    @patch("basic_memory.cli.commands.tool._recent_activity_json")
    @patch("basic_memory.cli.commands.tool.ConfigManager")
    def test_recent_activity_pagination_options(self, mock_config_manager, mock_activity_json):
        """Test recent-activity exposes --page and --page-size options."""
        # Mock config
        mock_config = MagicMock()
        mock_config.default_project = "test-project"
        mock_config_manager.return_value = mock_config
        mock_config_manager.return_value.get_project.return_value = ("test-project", "/tmp/test")

        # Mock the JSON helper
        mock_activity_data = {"results": [], "metadata": {}}
        mock_activity_json.return_value = mock_activity_data

        # Run command with pagination
        result = runner.invoke(
            cli_app,
            [
                "tool",
                "recent-activity",
                "--format",
                "json",
                "--page",
                "2",
                "--page-size",
                "25",
            ],
        )

        # Verify success
        assert result.exit_code == 0

        # Verify pagination params were passed to helper
        call_args = mock_activity_json.call_args
        assert call_args.kwargs["page"] == 2
        assert call_args.kwargs["page_size"] == 25

    @patch("basic_memory.cli.commands.tool._recent_activity_json")
    @patch("basic_memory.cli.commands.tool.ConfigManager")
    def test_recent_activity_project_param(self, mock_config_manager, mock_activity_json):
        """Test recent-activity --project works with --format json."""
        # Mock config
        mock_config = MagicMock()
        mock_config_manager.return_value = mock_config
        mock_config_manager.return_value.get_project.return_value = ("my-project", "/tmp/my-proj")

        # Mock the JSON helper
        mock_activity_data = {"results": [], "metadata": {}}
        mock_activity_json.return_value = mock_activity_data

        # Run command with project
        result = runner.invoke(
            cli_app,
            [
                "tool",
                "recent-activity",
                "--format",
                "json",
                "--project",
                "my-project",
            ],
        )

        # Verify success
        assert result.exit_code == 0

        # Verify project was resolved
        mock_config_manager.return_value.get_project.assert_called_with("my-project")


class TestBuildContextFormatFlag:
    """Tests for build-context --format json consistency."""

    @patch("basic_memory.cli.commands.tool.mcp_build_context")
    @patch("basic_memory.cli.commands.tool.ConfigManager")
    def test_build_context_accepts_format_flag(self, mock_config_manager, mock_build_context):
        """Test build-context accepts --format json for consistency."""
        # Mock config
        mock_config = MagicMock()
        mock_config.default_project = "test-project"
        mock_config_manager.return_value = mock_config
        mock_config_manager.return_value.get_project.return_value = ("test-project", "/tmp/test")

        # Mock the MCP tool
        mock_context_result = MagicMock()
        mock_context_result.model_dump.return_value = {
            "primary_result": {"title": "Test"},
            "related_results": [],
        }
        mock_build_context.fn.return_value = mock_context_result

        # Run command with format flag (build-context always outputs JSON)
        result = runner.invoke(
            cli_app,
            [
                "tool",
                "build-context",
                "memory://test",
                "--format",
                "json",
            ],
        )

        # Verify command accepts the flag without error
        assert result.exit_code == 0

        # Verify output is valid JSON
        output = json.loads(result.output)
        assert "primary_result" in output
