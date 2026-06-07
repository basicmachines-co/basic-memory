"""Integration test for `bm status --wait` against a real local project.

Unlike the unit tests in tests/cli/test_json_output.py (which mock get_status
to drive deterministic poll sequences), this exercises the full stack: the CLI
runs a real disk-vs-DB scan via the API/repository layer. After write-note
indexes a file, the project is already in sync, so --wait observes total == 0
on the first poll and exits 0 immediately.
"""

import json

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


def test_status_wait_returns_once_indexed(app, app_config, test_project, config_manager):
    """status --wait exits 0 with total == 0 when the project is fully indexed."""
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

    # --wait should observe a synced project (total == 0) and exit immediately.
    result = runner.invoke(cli_app, ["status", "--wait", "--json"])

    assert result.exit_code == 0, result.output
    start = result.output.index("{")
    data = json.loads(result.output[start:])
    assert data["total"] == 0
