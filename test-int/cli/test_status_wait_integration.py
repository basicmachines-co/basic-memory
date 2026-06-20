"""Integration test for `bm status --wait` against a real local project."""

import json

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


def test_status_wait_returns_once_indexed(app, app_config, test_project, config_manager):
    """status --wait exits 0 and returns the project-index observation."""
    # Write (and index) a note so the project has real content on disk + in DB.
    write_result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Wait Test Note",
            "--folder",
            "test-notes",
            "--content",
            "# Wait Test\n\nContent that should be indexed.",
        ],
    )
    assert write_result.exit_code == 0, write_result.output

    # --wait is a compatibility flag in the event-index flow and returns immediately.
    result = runner.invoke(cli_app, ["status", "--wait", "--json"])

    assert result.exit_code == 0, result.output
    start = result.output.index("{")
    data = json.loads(result.output[start:])
    assert data["total_files"] == 1
    assert data["observed_files"][0]["path"] == "test-notes/Wait Test Note.md"
