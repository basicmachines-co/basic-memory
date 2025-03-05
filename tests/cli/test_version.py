"""Tests for CLI sync command."""

from typer.testing import CliRunner

from basic_memory.cli.app import app

# Set up CLI runner
runner = CliRunner()


def test_version_arg():
    """Test the version arg."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
