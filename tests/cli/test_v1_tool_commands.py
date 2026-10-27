"""The CLI exposes the 1.0 verbs and rejects retired tool subcommands."""

import pytest
from typer.testing import CliRunner

from basic_memory.cli.commands.tool import tool_app
from basic_memory.cli.main import app

RETIRED_COMMANDS = (
    "write-note",
    "read-note",
    "edit-note",
    "delete-note",
    "build-context",
    "recent-activity",
    "search-notes",
    "list-projects",
    "list-workspaces",
    "schema-validate",
    "schema-infer",
    "schema-diff",
)


def test_tool_commands_use_plain_verbs():
    assert {command.name for command in tool_app.registered_commands} == {
        "write",
        "read",
        "edit",
        "delete",
        "context",
        "recent",
        "search",
        "projects",
        "workspaces",
        "validate",
        "infer",
        "diff",
    }


@pytest.mark.parametrize("name", RETIRED_COMMANDS)
def test_retired_tool_commands_are_not_aliases(name):
    result = CliRunner().invoke(app, ["tool", name, "--help"])
    assert result.exit_code == 2
    assert "No such command" in result.output
