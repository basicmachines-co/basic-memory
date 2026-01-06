"""Database management commands."""

import asyncio

import typer
from loguru import logger
from rich.console import Console
from sqlalchemy.exc import OperationalError

from basic_memory import db
from basic_memory.cli.app import app
from basic_memory.config import ConfigManager, BasicMemoryConfig, save_basic_memory_config

console = Console()


@app.command()
def reset(
    reindex: bool = typer.Option(False, "--reindex", help="Rebuild db index from filesystem"),
):  # pragma: no cover
    """Reset database (drop all tables and recreate)."""
    console.print(
        "[yellow]Note:[/yellow] This only deletes the index database. "
        "Your markdown note files will not be affected."
    )
    if typer.confirm("Reset the database index? (You can rebuild it with 'bm sync')"):
        logger.info("Resetting database...")
        config_manager = ConfigManager()
        app_config = config_manager.config
        # Get database path
        db_path = app_config.app_database_path

        # Delete the database file and WAL files if they exist
        for suffix in ["", "-shm", "-wal"]:
            path = db_path.parent / f"{db_path.name}{suffix}"
            if path.exists():
                try:
                    path.unlink()
                    logger.info(f"Deleted: {path}")
                except OSError as e:
                    console.print(
                        f"[red]Error:[/red] Cannot delete {path.name}: {e}\n"
                        "The database may be in use by another process (e.g., MCP server).\n"
                        "Please close Claude Desktop or any other Basic Memory clients and try again."
                    )
                    raise typer.Exit(1)

        # Reset project configuration
        config = BasicMemoryConfig()
        save_basic_memory_config(config_manager.config_file, config)
        logger.info("Project configuration reset to default")

        # Create a new empty database
        try:
            asyncio.run(db.run_migrations(app_config))
        except OperationalError as e:
            if "disk I/O error" in str(e) or "database is locked" in str(e):
                console.print(
                    "[red]Error:[/red] Cannot access database. "
                    "It may be in use by another process (e.g., MCP server).\n"
                    "Please close Claude Desktop or any other Basic Memory clients and try again."
                )
                raise typer.Exit(1)
            raise
        logger.info("Database reset complete")

        if reindex:
            # Run database sync directly
            from basic_memory.cli.commands.command_utils import run_sync

            logger.info("Rebuilding search index from filesystem...")
            asyncio.run(run_sync(project=None))
