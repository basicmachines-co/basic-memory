"""CLI compact discovery keeps identifiers without loading source prose."""

import json

import pytest
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app


@pytest.mark.parametrize("command", ["search-notes", "build-context"])
@pytest.mark.parametrize("mode", ["--json", "--plain"])
def test_compact_cli_discovery(app, app_config, test_project, config_manager, command, mode):
    runner = CliRunner()
    body = "Source prose that should be omitted. " * 100
    written = runner.invoke(
        cli_app,
        ["tool", "write-note", "--title", "CompactCli", "--folder", "notes"],
        input=body,
    )
    assert written.exit_code == 0, written.output
    note = json.loads(written.stdout)
    query = "CompactCli" if command == "search-notes" else note["permalink"]
    result = runner.invoke(cli_app, ["tool", command, query, "--compact", mode])
    assert result.exit_code == 0, result.output
    assert "CompactCli" in result.stdout
    assert "Source prose" not in result.stdout
    if mode == "--json":
        data = json.loads(result.stdout)
        assert data["results"]
    full = runner.invoke(cli_app, ["tool", command, query, mode])
    assert full.exit_code == 0, full.output
    assert "Source prose" in full.stdout
