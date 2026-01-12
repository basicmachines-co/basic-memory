"""Integration tests for version command."""

from typer.testing import CliRunner

from basic_memory.cli.main import app
import basic_memory


def test_version_command():
    """Test 'bm --version' command shows version."""
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert basic_memory.__version__ in result.stdout
