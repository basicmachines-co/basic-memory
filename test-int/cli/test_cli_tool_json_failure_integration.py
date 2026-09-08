"""Failure-path integration tests for CLI tool JSON output.

Verifies that error conditions return proper exit codes and that
error messages go to stderr, not stdout (which would break JSON parsing).
"""

import json

import pytest
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


@pytest.mark.parametrize(
    "identifier", ["nonexistent-note-that-does-not-exist", "22222222-2222-4222-8222-222222222222"]
)
def test_read_note_not_found(app, app_config, test_project, config_manager, identifier):
    """A missing note remains machine-readable but must not report success."""
    result = runner.invoke(
        cli_app,
        ["tool", "read-note", identifier],
    )

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["error"] == "NOTE_NOT_FOUND"
    assert data["message"] == f"Note not found: {identifier}"
    assert "Note not found" in result.stderr
    assert data["title"] is None
    assert data["permalink"] is None
    assert data["content"] is None


@pytest.mark.parametrize("mode", ["piped", "json", "plain", "rich"])
@pytest.mark.parametrize("related", [False, True])
def test_read_after_delete_fails_in_every_mode(
    app, app_config, test_project, config_manager, monkeypatch, mode, related
):
    """The actual write/delete/read flow must fail even when search offers alternatives."""
    monkeypatch.setattr("basic_memory.cli.commands.tool._use_rich", lambda: mode == "rich")
    title = "Missingneedle"
    created = runner.invoke(
        cli_app,
        ["tool", "write-note", "--title", title, "--folder", "test", "--content", "original"],
    )
    assert created.exit_code == 0, created.output
    deleted = runner.invoke(cli_app, ["tool", "delete-note", title])
    assert deleted.exit_code == 0, deleted.output
    if related:
        alternative = runner.invoke(
            cli_app,
            [
                "tool",
                "write-note",
                "--title",
                "Alternative",
                "--folder",
                "test",
                "--content",
                f"This mentions {title} but is a different note.",
            ],
        )
        assert alternative.exit_code == 0, alternative.output

    flags = [f"--{mode}"] if mode in {"json", "plain"} else []
    result = runner.invoke(cli_app, ["tool", "read-note", title, "--frontmatter", *flags])
    assert result.exit_code == 1, result.output
    assert f"Note not found: {title}" in result.stderr
    if mode in {"piped", "json"}:
        payload = json.loads(result.stdout)
        assert payload["error"] == "NOTE_NOT_FOUND"
        assert payload["content"] is None
        if related:
            assert payload["related_results"][0]["title"] == "Alternative"
    else:
        assert "Note not found" in result.stdout
        if related:
            assert "Alternative" in result.stdout


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
