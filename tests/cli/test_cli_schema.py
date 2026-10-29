"""Tests for CLI schema command plumbing.

The commands run end to end against a real project in
test-int/cli/test_schema_commands_integration.py.
"""

import pytest
from typer.testing import CliRunner

from basic_memory.cli.commands.command_utils import run_schema_tool
from basic_memory.cli.main import app as cli_app
from basic_memory.mcp.refusals import Refusal, refusal

runner = CliRunner()


def test_schema_routing_both_flags_error():
    """Schema commands exit with error when both --local and --cloud are specified."""
    result = runner.invoke(
        cli_app,
        ["schema", "validate", "person", "--local", "--cloud"],
    )

    assert result.exit_code == 1


def test_run_schema_tool_keeps_nothing_to_report_and_reraises_the_rest():
    """NOT_FOUND is "nothing to report" for the CLI; any other refusal still fails."""

    async def nothing() -> None:
        raise refusal("NOT_FOUND", "No schemas defined in this project", output_format="json")

    async def broken() -> None:
        raise refusal("INVALID_ARGUMENTS", "bad", output_format="json")

    assert run_schema_tool(nothing()) == {"error": "No schemas defined in this project"}
    with pytest.raises(Refusal):
        run_schema_tool(broken())
