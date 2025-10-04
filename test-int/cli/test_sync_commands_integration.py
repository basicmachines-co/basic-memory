"""Integration tests for sync CLI commands."""

from pathlib import Path
from typer.testing import CliRunner

from basic_memory.cli.main import app


def test_sync_command(app_config, test_project, config_manager, config_home):
    """Test 'bm sync' command successfully syncs files."""
    runner = CliRunner()

    # Create a test file
    test_file = Path(config_home) / "test-note.md"
    test_file.write_text("# Test Note\n\nThis is a test.")

    # Run sync
    result = runner.invoke(app, ["sync", "--project", "test-project"])

    if result.exit_code != 0:
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
    assert result.exit_code == 0
    assert "sync" in result.stdout.lower() or "initiated" in result.stdout.lower()


def test_status_command(app_config, test_project, config_manager, config_home):
    """Test 'bm status' command shows sync status."""
    runner = CliRunner()

    # Create a test file
    test_file = Path(config_home) / "unsynced.md"
    test_file.write_text("# Unsynced Note\n\nThis file hasn't been synced yet.")

    # Run status
    result = runner.invoke(app, ["status", "--project", "test-project"])

    if result.exit_code != 0:
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
    assert result.exit_code == 0
    # Should show some status output
    assert len(result.stdout) > 0


def test_status_verbose(app_config, test_project, config_manager, config_home):
    """Test 'bm status --verbose' shows detailed status."""
    runner = CliRunner()

    # Create a test file
    test_file = Path(config_home) / "test.md"
    test_file.write_text("# Test\n\nContent.")

    # Run status with verbose
    result = runner.invoke(app, ["status", "--project", "test-project", "--verbose"])

    if result.exit_code != 0:
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
    assert result.exit_code == 0
    assert len(result.stdout) > 0
