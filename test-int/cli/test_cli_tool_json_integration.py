"""Integration tests for CLI tool --format json output."""

import json

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


def test_write_note_json_format(app, app_config, test_project, config_manager):
    """Test write-note --format json returns valid JSON with expected keys."""
    result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Integration Test Note",
            "--folder",
            "test-notes",
            "--content",
            "# Test\n\nThis is test content.",
            "--format",
            "json",
        ],
    )

    if result.exit_code != 0:
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr if hasattr(result, 'stderr') else 'N/A'}")
        print(f"Exception: {result.exception}")
    assert result.exit_code == 0

    data = json.loads(result.stdout)
    assert data["title"] == "Integration Test Note"
    assert "permalink" in data
    assert data["content"] == "# Test\n\nThis is test content."
    assert "file_path" in data


def test_read_note_json_format(app, app_config, test_project, config_manager):
    """Test read-note --format json returns valid JSON with expected keys."""
    # First, write a note
    write_result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Read Test Note",
            "--folder",
            "test-notes",
            "--content",
            "# Read Test\n\nContent to read back.",
            "--format",
            "json",
        ],
    )
    assert write_result.exit_code == 0
    write_data = json.loads(write_result.stdout)
    permalink = write_data["permalink"]

    # Now read it back
    result = runner.invoke(
        cli_app,
        ["tool", "read-note", permalink, "--format", "json"],
    )

    if result.exit_code != 0:
        print(f"STDOUT: {result.stdout}")
        print(f"Exception: {result.exception}")
    assert result.exit_code == 0

    data = json.loads(result.stdout)
    assert data["title"] == "Read Test Note"
    assert data["permalink"] == permalink
    assert "content" in data
    assert "file_path" in data


def test_recent_activity_json_format(app, app_config, test_project, config_manager, monkeypatch):
    """Test recent-activity --format json returns valid JSON list."""
    # _recent_activity_json uses resolve_project_parameter which requires either
    # default_project_mode=True or BASIC_MEMORY_MCP_PROJECT to resolve a project
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", test_project.name)

    # Write a note to ensure there's recent activity
    write_result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Activity Test Note",
            "--folder",
            "test-notes",
            "--content",
            "# Activity\n\nTest content for activity.",
            "--format",
            "json",
        ],
    )
    assert write_result.exit_code == 0

    # Get recent activity
    result = runner.invoke(
        cli_app,
        ["tool", "recent-activity", "--format", "json"],
    )

    if result.exit_code != 0:
        print(f"STDOUT: {result.stdout}")
        print(f"Exception: {result.exception}")
    assert result.exit_code == 0

    data = json.loads(result.stdout)
    assert isinstance(data, list)
    # Should have at least one entity from the note we just wrote
    assert len(data) > 0
    item = data[0]
    assert "title" in item
    assert "permalink" in item
    assert "file_path" in item
    assert "created_at" in item
