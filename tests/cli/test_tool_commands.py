"""`bm tool` mirrors the bm_* MCP tools: one subcommand per tool, flags named after
the tool's parameters."""

import inspect

import pytest

from basic_memory.cli.commands.tool import tool_app
from basic_memory.mcp import tools

# Parameters every subcommand adds for the shell, not passed to the tool.
SHELL_ONLY = {"json_output", "local", "cloud"}
# Tool parameters the shell sets itself.
SET_BY_SHELL = {"context", "output_format"}

COMMAND_TOOLS = {
    "read": tools.bm_read,
    "write": tools.bm_write,
    "edit": tools.bm_edit,
    "move": tools.bm_move,
    "delete": tools.bm_delete,
    "list": tools.bm_list,
    "find": tools.bm_find,
    "search": tools.bm_search,
    "context": tools.bm_context,
    "project": tools.bm_project,
    "schema": tools.bm_schema,
}


def test_one_subcommand_per_tool() -> None:
    assert {command.name for command in tool_app.registered_commands} == set(COMMAND_TOOLS)


@pytest.mark.parametrize("name", sorted(COMMAND_TOOLS))
def test_flags_are_the_tool_parameters(name: str) -> None:
    command = next(command for command in tool_app.registered_commands if command.name == name)
    assert command.callback is not None
    cli_parameters = set(inspect.signature(command.callback).parameters) - SHELL_ONLY
    tool_parameters = set(inspect.signature(COMMAND_TOOLS[name]).parameters) - SET_BY_SHELL

    assert cli_parameters == tool_parameters
