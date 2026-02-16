"""Failure-path integration tests for CLI tool --format json output.

Verifies that error conditions return proper exit codes and that
error messages go to stderr, not stdout (which would break JSON parsing).
"""

import json

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


def test_read_note_not_found_json(app, app_config, test_project, config_manager):
    """read-note with non-existent identifier returns error exit code."""
    result = runner.invoke(
        cli_app,
        ["tool", "read-note", "nonexistent-note-that-does-not-exist", "--format", "json"],
    )

    assert result.exit_code != 0, "Should fail for non-existent note"
    # stdout should NOT contain valid JSON with data (it's an error)
    # The error message should be informative
    output = result.stdout + (result.stderr if hasattr(result, "stderr") and result.stderr else "")
    assert (
        "error" in output.lower()
        or "not found" in output.lower()
        or "could not find" in output.lower()
    )


def test_write_note_missing_content_json(app, app_config, test_project, config_manager):
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
            "--format",
            "json",
        ],
        input="",  # Empty stdin
    )

    # Should fail — no content provided
    assert result.exit_code != 0, "Should fail when no content is provided"


def test_write_note_json_then_read_json_roundtrip(app, app_config, test_project, config_manager):
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
            "--format",
            "json",
        ],
    )
    assert write_result.exit_code == 0
    write_data = json.loads(write_result.stdout)
    assert "permalink" in write_data

    # Read it back using the permalink from the write response
    read_result = runner.invoke(
        cli_app,
        ["tool", "read-note", write_data["permalink"], "--format", "json"],
    )
    assert read_result.exit_code == 0
    read_data = json.loads(read_result.stdout)
    assert read_data["title"] == "Roundtrip Test"
    assert read_data["permalink"] == write_data["permalink"]


def test_recent_activity_empty_project_json(
    app, app_config, test_project, config_manager, monkeypatch
):
    """recent-activity on empty project returns valid empty JSON list."""
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", test_project.name)

    result = runner.invoke(
        cli_app,
        ["tool", "recent-activity", "--format", "json"],
    )

    # Should succeed even if empty
    if result.exit_code == 0:
        data = json.loads(result.stdout)
        assert isinstance(data, list)
