"""Tests for database CLI commands."""

from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app


def test_reset_command_clears_project_configuration(config_manager, app_config):
    """Test that the reset command clears project configuration to defaults."""
    runner = CliRunner()
    
    # Set up initial configuration with multiple projects
    config_manager.config.projects = {
        "project1": "/path/to/project1",
        "project2": "/path/to/project2",
        "test-project": "/path/to/test"
    }
    config_manager.config.default_project = "project1"
    
    # Mock the confirmation to return True (user confirms reset)
    with patch("basic_memory.cli.commands.db.typer.confirm", return_value=True):
        # Mock the database file existence check and deletion
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.unlink") as mock_unlink:
                # Mock the async db.run_migrations function
                with patch("basic_memory.cli.commands.db.asyncio.run") as mock_run:
                    mock_run.return_value = None
                    
                    # Run the reset command
                    result = runner.invoke(cli_app, ["db", "reset"])
                    
                    # Should exit successfully
                    assert result.exit_code == 0
                    
                    # Database file should be deleted
                    mock_unlink.assert_called_once()
                    
                    # Async migrations should be called
                    mock_run.assert_called_once()
                    
                    # Configuration should be reset to defaults
                    expected_projects = {"main": str(Path.home() / "basic-memory")}
                    assert config_manager.config.projects == expected_projects
                    assert config_manager.config.default_project == "main"


def test_reset_command_user_cancels(config_manager):
    """Test that the reset command does nothing when user cancels."""
    runner = CliRunner()
    
    # Set up initial configuration
    original_projects = {
        "project1": "/path/to/project1",
        "project2": "/path/to/project2"
    }
    config_manager.config.projects = original_projects.copy()
    config_manager.config.default_project = "project1"
    
    # Mock the confirmation to return False (user cancels)
    with patch("basic_memory.cli.commands.db.typer.confirm", return_value=False):
        with patch("pathlib.Path.unlink") as mock_unlink:
            with patch("basic_memory.cli.commands.db.asyncio.run") as mock_run:
                # Run the reset command
                result = runner.invoke(cli_app, ["db", "reset"])
                
                # Should exit successfully
                assert result.exit_code == 0
                
                # No database operations should occur
                mock_unlink.assert_not_called()
                mock_run.assert_not_called()
                
                # Configuration should remain unchanged
                assert config_manager.config.projects == original_projects
                assert config_manager.config.default_project == "project1"


def test_reset_command_with_reindex(config_manager):
    """Test that the reset command with --reindex flag calls sync."""
    runner = CliRunner()
    
    # Mock the confirmation to return True
    with patch("basic_memory.cli.commands.db.typer.confirm", return_value=True):
        # Mock database operations
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.unlink"):
                with patch("basic_memory.cli.commands.db.asyncio.run"):
                    # Mock the sync command that gets imported and called
                    with patch("basic_memory.cli.commands.db.sync") as mock_sync:
                        # Run the reset command with reindex flag
                        result = runner.invoke(cli_app, ["db", "reset", "--reindex"])
                        
                        # Should exit successfully
                        assert result.exit_code == 0
                        
                        # Sync should be called with watch=False
                        mock_sync.assert_called_once_with(watch=False)


def test_reset_command_nonexistent_database(config_manager):
    """Test reset command when database file doesn't exist."""
    runner = CliRunner()
    
    # Mock the confirmation to return True
    with patch("basic_memory.cli.commands.db.typer.confirm", return_value=True):
        # Mock database file doesn't exist
        with patch("pathlib.Path.exists", return_value=False):
            with patch("pathlib.Path.unlink") as mock_unlink:
                with patch("basic_memory.cli.commands.db.asyncio.run") as mock_run:
                    # Run the reset command
                    result = runner.invoke(cli_app, ["db", "reset"])
                    
                    # Should exit successfully
                    assert result.exit_code == 0
                    
                    # Database file deletion should not be attempted
                    mock_unlink.assert_not_called()
                    
                    # But migrations should still run
                    mock_run.assert_called_once()
                    
                    # Configuration should still be reset
                    expected_projects = {"main": str(Path.home() / "basic-memory")}
                    assert config_manager.config.projects == expected_projects
                    assert config_manager.config.default_project == "main"