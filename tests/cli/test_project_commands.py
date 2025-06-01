"""Tests for the project CLI commands."""

import os
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app


@patch("basic_memory.cli.commands.project.asyncio.run")
def test_project_list_command(mock_run, cli_env):
    """Test the 'project list' command with mocked API."""
    # Mock the API response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "projects": [{"name": "test", "path": "/path/to/test", "is_default": True}],
        "default_project": "test",
        "current_project": "test",
    }
    mock_run.return_value = mock_response

    runner = CliRunner()
    result = runner.invoke(cli_app, ["project", "list"])

    # Just verify it runs without exception
    assert result.exit_code == 0


@patch("basic_memory.cli.commands.project.asyncio.run")
def test_project_add_command(mock_run, cli_env):
    """Test the 'project add' command with mocked API."""
    # Mock the API response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": "Project 'test-project' added successfully",
        "status": "success",
        "default": False,
    }
    mock_run.return_value = mock_response

    runner = CliRunner()
    result = runner.invoke(cli_app, ["project", "add", "test-project", "/path/to/project"])

    # Just verify it runs without exception
    assert result.exit_code == 0


@patch("basic_memory.cli.commands.project.asyncio.run")
def test_project_remove_command(mock_run, cli_env):
    """Test the 'project remove' command with mocked API."""
    # Mock the API response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": "Project 'test-project' removed successfully",
        "status": "success",
        "default": False,
    }
    mock_run.return_value = mock_response

    runner = CliRunner()
    result = runner.invoke(cli_app, ["project", "remove", "test-project"])

    # Just verify it runs without exception
    assert result.exit_code == 0


@patch("basic_memory.cli.commands.project.asyncio.run")
@patch("importlib.reload")
def test_project_default_command(mock_reload, mock_run, cli_env):
    """Test the 'project default' command with mocked API."""
    # Mock the API response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": "Project 'test-project' set as default successfully",
        "status": "success",
        "default": True,
    }
    mock_run.return_value = mock_response

    # Mock necessary config methods to have the test-project handled
    # Patching call_put directly since it's imported at the module level

    # Patch the os.environ for checking
    with patch.dict(os.environ, {}, clear=True):
        # Patch ConfigManager.set_default_project to prevent validation error
        with patch("basic_memory.config.ConfigManager.set_default_project"):
            runner = CliRunner()
            result = runner.invoke(cli_app, ["project", "default", "test-project"])

            # Just verify it runs without exception and environment is set
            assert result.exit_code == 0
            assert "BASIC_MEMORY_PROJECT" in os.environ
            assert os.environ["BASIC_MEMORY_PROJECT"] == "test-project"


@patch("basic_memory.cli.commands.project.asyncio.run")
def test_project_sync_command(mock_run, cli_env):
    """Test the 'project sync' command with mocked API."""
    # Mock the API response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": "Projects synchronized successfully between configuration and database",
        "status": "success",
        "default": False,
    }
    mock_run.return_value = mock_response

    runner = CliRunner()
    result = runner.invoke(cli_app, ["project", "sync"])

    # Just verify it runs without exception
    assert result.exit_code == 0


@patch("basic_memory.cli.commands.project.asyncio.run")
def test_project_failure_exits_with_error(mock_run, cli_env):
    """Test that CLI commands properly exit with error code on API failures."""
    # Mock an exception being raised
    mock_run.side_effect = Exception("API server not running")

    runner = CliRunner()

    # Test various commands for proper error handling
    list_result = runner.invoke(cli_app, ["project", "list"])
    add_result = runner.invoke(cli_app, ["project", "add", "test-project", "/path/to/project"])
    remove_result = runner.invoke(cli_app, ["project", "remove", "test-project"])
    default_result = runner.invoke(cli_app, ["project", "default", "test-project"])

    # All should exit with code 1 and show error message
    assert list_result.exit_code == 1
    assert "Error listing projects" in list_result.output
    assert "Make sure the Basic Memory server is running" in list_result.output

    assert add_result.exit_code == 1
    assert "Error adding project" in add_result.output

    assert remove_result.exit_code == 1
    assert "Error removing project" in remove_result.output

    assert default_result.exit_code == 1
    assert "Error setting default project" in default_result.output
