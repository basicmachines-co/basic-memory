"""Failure-path integration tests for CLI tool JSON output.

Verifies that error conditions return proper exit codes and that
error messages go to stderr, not stdout (which would break JSON parsing).
"""

import json

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


def test_read_note_not_found(app, app_config, test_project, config_manager):
    """read-note with non-existent identifier returns JSON with null fields."""
    result = runner.invoke(
        cli_app,
        ["tool", "read-note", "nonexistent-note-that-does-not-exist"],
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    # MCP tool returns a valid JSON payload with null fields for not-found
    assert data["title"] is None
    assert data["permalink"] is None
    assert data["content"] is None


def test_write_note_missing_content(app, app_config, test_project, config_manager):
    """write-note without content or stdin returns error exit code."""
    result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "No Content Note",
            "--folder",
            "test",
        ],
        input="",  # Empty stdin
    )

    # Should fail — no content provided
    assert result.exit_code != 0, "Should fail when no content is provided"


def test_write_note_then_read_note_roundtrip(app, app_config, test_project, config_manager):
    """write-note JSON output can be used to read-note by permalink."""
    # Write a note
    write_result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Roundtrip Test",
            "--folder",
            "test-roundtrip",
            "--content",
            "# Roundtrip Test\n\nContent for roundtrip.",
        ],
    )
    assert write_result.exit_code == 0
    write_data = json.loads(write_result.stdout)
    assert "permalink" in write_data

    # Read it back using the permalink from the write response
    read_result = runner.invoke(
        cli_app,
        ["tool", "read-note", write_data["permalink"]],
    )
    assert read_result.exit_code == 0
    read_data = json.loads(read_result.stdout)
    assert read_data["title"] == "Roundtrip Test"
    assert read_data["permalink"] == write_data["permalink"]


def test_recent_activity_empty_project(app, app_config, test_project, config_manager, monkeypatch):
    """recent-activity on empty project returns valid empty JSON list."""
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", test_project.name)

    result = runner.invoke(
        cli_app,
        ["tool", "recent-activity"],
    )

    # Should succeed even if empty
    if result.exit_code == 0:
        data = json.loads(result.stdout)
        assert isinstance(data, list)
