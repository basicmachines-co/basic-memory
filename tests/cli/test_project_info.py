"""Tests for the project_info CLI command."""

import json

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app


def test_info_stats_command(cli_env, test_graph):
    """Test the 'info stats' command with default output."""
    runner = CliRunner()

    # Run the command
    result = runner.invoke(cli_app, ["info", "stats"])

    # Verify exit code
    assert result.exit_code == 0

    # Check that key data is included in the output
    assert "Basic Memory Project Info" in result.stdout


def test_info_stats_json(cli_env, test_graph):
    """Test the 'info stats --json' command for JSON output."""
    runner = CliRunner()

    # Run the command with --json flag
    result = runner.invoke(cli_app, ["info", "stats", "--json"])

    # Verify exit code
    assert result.exit_code == 0

    # Parse JSON output
    output = json.loads(result.stdout)

    # Verify JSON structure matches our sample data
    assert output["project_name"] == "main"
