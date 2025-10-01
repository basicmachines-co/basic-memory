"""Command module for basic-memory sync operations."""

import asyncio
from typing import Annotated, Optional

import typer

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_sync


@app.command()
def sync(
    project: Annotated[
        Optional[str],
        typer.Option(help="The project name."),
    ] = None,
) -> None:
    """Sync knowledge files with the database."""
    # Run sync via API
    asyncio.run(run_sync(project))
