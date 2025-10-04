"""Command module for basic-memory sync operations."""

import asyncio
from typing import Annotated, Optional

import typer

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_sync
from basic_memory.config import ConfigManager


@app.command()
def sync(
    project: Annotated[
        Optional[str],
        typer.Option(help="The project name."),
    ] = None,
    watch: Annotated[
        bool,
        typer.Option("--watch", help="Run continuous sync (cloud mode only)"),
    ] = False,
    interval: Annotated[
        int,
        typer.Option("--interval", help="Sync interval in seconds for watch mode (default: 60)"),
    ] = 60,
) -> None:
    """Sync knowledge files with the database.

    In local mode: Scans filesystem and updates database.
    In cloud mode: Runs bidirectional file sync (bisync) then updates database.

    Examples:
      bm sync                    # One-time sync
      bm sync --watch            # Continuous sync every 60s
      bm sync --watch --interval 30  # Continuous sync every 30s
    """
    config = ConfigManager().config

    if config.cloud_mode_enabled:
        # Cloud mode: run bisync which includes database sync
        from basic_memory.cli.commands.cloud.bisync_commands import run_bisync, run_bisync_watch

        try:
            if watch:
                run_bisync_watch(interval_seconds=interval)
            else:
                run_bisync()
        except Exception:
            raise typer.Exit(1)
    else:
        # Local mode: just database sync
        if watch:
            typer.echo(
                "Error: --watch is only available in cloud mode. Run 'bm cloud login' first."
            )
            raise typer.Exit(1)

        asyncio.run(run_sync(project))
