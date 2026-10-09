"""Tests for the top-level CLI app wiring."""

import typer
from typer.core import TyperGroup

from basic_memory.cli.app import SKIP_INIT_COMMANDS

# Importing main registers every command group on the shared app.
from basic_memory.cli.main import app


def test_skip_init_commands_are_registered_top_level_commands():
    """A stale entry silently stops matching anything (#1593: `sync`, `watch`)."""
    root = typer.main.get_command(app)
    assert isinstance(root, TyperGroup)

    assert SKIP_INIT_COMMANDS <= set(root.commands), SKIP_INIT_COMMANDS - set(root.commands)
