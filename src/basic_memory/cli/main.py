"""Main CLI entry point for basic-memory."""  # pragma: no cover

import asyncio

import typer

from basic_memory.cli.app import app  # pragma: no cover

# Register commands
from basic_memory.cli.commands import (  # noqa: F401  # pragma: no cover
    db,
    import_chatgpt,
    import_claude_conversations,
    import_claude_projects,
    import_memory_json,
    mcp,
    project,
    status,
    sync,
    tool,
)
from basic_memory.config import config
from basic_memory.db import run_migrations as db_run_migrations


# Version command
@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    project: str = typer.Option(  # noqa
        "main",
        "--project",
        "-p",
        help="Specify which project to use",
        envvar="BASIC_MEMORY_PROJECT",
    ),
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show version information and exit.",
        is_eager=True,
    ),
):
    """Basic Memory - Local-first personal knowledge management system."""
    if version:  # pragma: no cover
        from basic_memory import __version__
        from basic_memory.config import config

        typer.echo(f"Basic Memory v{__version__}")
        typer.echo(f"Current project: {config.project}")
        typer.echo(f"Project path: {config.home}")
        raise typer.Exit()

    # Handle project selection via environment variable
    if project:
        import os

        os.environ["BASIC_MEMORY_PROJECT"] = project


if __name__ == "__main__":  # pragma: no cover
    # Run database migrations
    asyncio.run(db_run_migrations(config))

    # start the app
    app()
