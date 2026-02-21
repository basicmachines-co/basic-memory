"""Integration tests for CLI tool JSON output."""

import json

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


def test_write_note_json_output(app, app_config, test_project, config_manager):
    """write-note returns valid JSON with expected keys."""
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
    assert "file_path" in data


def test_read_note_json_output(app, app_config, test_project, config_manager):
    """read-note returns valid JSON with expected keys."""
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
        ],
    )
    assert write_result.exit_code == 0
    write_data = json.loads(write_result.stdout)
    permalink = write_data["permalink"]

    # Now read it back
    result = runner.invoke(
        cli_app,
        ["tool", "read-note", permalink],
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


def test_read_note_include_frontmatter(app, app_config, test_project, config_manager):
    """read-note --include-frontmatter includes frontmatter in output."""
    write_result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Read Frontmatter Note",
            "--folder",
            "test-notes",
            "--content",
            "# Read Frontmatter Note\n\nFrontmatter test content.",
        ],
    )
    assert write_result.exit_code == 0
    write_data = json.loads(write_result.stdout)

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "read-note",
            write_data["permalink"],
            "--include-frontmatter",
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["title"] == "Read Frontmatter Note"
    assert data["permalink"] == write_data["permalink"]
    assert "content" in data


def test_recent_activity_json_output(app, app_config, test_project, config_manager, monkeypatch):
    """recent-activity returns valid JSON list."""
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
        ],
    )
    assert write_result.exit_code == 0

    # Get recent activity
    result = runner.invoke(
        cli_app,
        ["tool", "recent-activity"],
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
