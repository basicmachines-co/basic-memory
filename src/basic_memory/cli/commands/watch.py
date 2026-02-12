"""Watch command - run file watcher as a standalone long-running process."""

import asyncio
import os
import signal
import sys
from typing import Optional

import typer
from loguru import logger

from basic_memory import db
from basic_memory.cli.app import app
from basic_memory.cli.container import get_container
from basic_memory.config import ConfigManager
from basic_memory.services.initialization import initialize_app
from basic_memory.sync.coordinator import SyncCoordinator


async def run_watch(project: Optional[str] = None) -> None:
    """Run the file watcher as a long-running process.

    This is the async core of the watch command. It:
    1. Initializes the app (DB migrations + project reconciliation)
    2. Validates and sets project constraint if --project given
    3. Creates a SyncCoordinator with quiet=False for Rich console output
    4. Blocks until SIGINT/SIGTERM, then shuts down cleanly
    """
    container = get_container()
    config = container.config

    # --- Initialization ---
    await initialize_app(config)

    # --- Project constraint ---
    if project:
        config_manager = ConfigManager()
        project_name, _ = config_manager.get_project(project)
        if not project_name:
            typer.echo(f"No project found named: {project}", err=True)
            raise typer.Exit(1)

        os.environ["BASIC_MEMORY_MCP_PROJECT"] = project_name
        logger.info(f"Watch constrained to project: {project_name}")

    # --- Sync coordinator ---
    # quiet=False so file change events are printed to the terminal
    sync_coordinator = SyncCoordinator(config=config, should_sync=True, quiet=False)

    # --- Signal handling ---
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # --- Run ---
    try:
        await sync_coordinator.start()
        logger.info("Watch service running, press Ctrl+C to stop")
        await shutdown_event.wait()
    finally:
        await sync_coordinator.stop()
        await db.shutdown_db()
        logger.info("Watch service stopped")


@app.command()
def watch(
    project: Optional[str] = typer.Option(None, help="Restrict watcher to a single project"),
) -> None:
    """Run file watcher as a long-running process (no MCP server).

    Watches for file changes in project directories and syncs them to the
    database. Useful for running Basic Memory sync alongside external tools
    that don't use the MCP server.
    """
    # On Windows, use SelectorEventLoop to avoid ProactorEventLoop cleanup issues
    if sys.platform == "win32":  # pragma: no cover
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(run_watch(project=project))
