"""Tests for CLI sync command."""

from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from basic_memory.cli.app import app

# Set up CLI runner
runner = CliRunner()


def test_sync_command():
    """Test the sync command calls run_sync correctly."""
    with patch("basic_memory.cli.commands.sync.run_sync", new_callable=AsyncMock) as mock_run_sync:
        mock_run_sync.return_value = None

        result = runner.invoke(app, ["sync"])
        assert result.exit_code == 0

        # Verify the function was called with project=None
        mock_run_sync.assert_called_once_with(None)


def test_sync_command_with_project():
    """Test the sync command with project parameter."""
    with patch("basic_memory.cli.commands.sync.run_sync", new_callable=AsyncMock) as mock_run_sync:
        mock_run_sync.return_value = None

        result = runner.invoke(app, ["sync", "--project", "my-project"])
        assert result.exit_code == 0

        # Verify the function was called with the project name
        mock_run_sync.assert_called_once_with("my-project")


def test_sync_command_error():
    """Test the sync command error handling."""
    from mcp.server.fastmcp.exceptions import ToolError

    with patch(
        "basic_memory.cli.commands.command_utils.run_sync", new_callable=AsyncMock
    ) as mock_run_sync:
        # Mock a ToolError which is handled by run_sync
        mock_run_sync.side_effect = ToolError("Sync failed")

        result = runner.invoke(app, ["sync"])
        assert result.exit_code == 1
