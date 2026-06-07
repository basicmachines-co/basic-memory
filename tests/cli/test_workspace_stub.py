"""Tests for the top-level `bm workspace` stub (issue #821).

The stub redirects users to `bm cloud workspace` instead of letting Typer emit a
bare "No such command 'workspace'." with exit code 2.
"""

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app

# Importing the module registers the workspace_stub command on the top-level app.
import basic_memory.cli.commands.workspace  # noqa: F401

runner = CliRunner()


@pytest.mark.parametrize(
    "argv",
    [
        ["workspace"],
        ["workspace", "list"],
        ["workspace", "set-default", "foo"],
    ],
)
def test_workspace_stub_redirects_to_cloud_workspace(argv):
    """Every `bm workspace ...` form exits 1 and points to `bm cloud workspace`."""
    result = runner.invoke(app, argv)

    assert result.exit_code == 1, result.output
    assert "bm cloud workspace" in result.output
