"""CLI compact discovery keeps identifiers without loading source prose."""

import json

import pytest
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app


@pytest.mark.parametrize("command", ["search", "context"])
@pytest.mark.parametrize("output", [["--json"], []], ids=["json", "text"])
def test_compact_cli_discovery(app, app_config, test_project, config_manager, command, output):
    runner = CliRunner()
    body = "Source prose that should be omitted. " * 100
    written = runner.invoke(
        cli_app,
        ["tool", "write", "--title", "CompactCli", "--directory", "notes", "--json"],
        input=body,
    )
    assert written.exit_code == 0, written.output
    note = json.loads(written.stdout)
    query = "CompactCli" if command == "search" else note["permalink"]
    result = runner.invoke(cli_app, ["tool", command, query, "--compact", *output])
    assert result.exit_code == 0, result.output
    assert "CompactCli" in result.stdout
    assert "Source prose" not in result.stdout
    if output:
        data = json.loads(result.stdout)
        assert data["results"]
    full = runner.invoke(cli_app, ["tool", command, query, *output])
    assert full.exit_code == 0, full.output
    # bm_search's text result lists matches without bodies; JSON and context carry them.
    if output or command == "context":
        assert "Source prose" in full.stdout
