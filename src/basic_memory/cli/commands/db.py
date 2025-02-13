"""Database management commands."""

import asyncio
import typer
from loguru import logger

from basic_memory.alembic import migrations
from basic_memory.cli.app import app


@app.command()
def reset(
    reindex: bool = typer.Option(False, "--reindex", help="Rebuild indices from filesystem"),
):  # pragma: no cover
    """Reset database (drop all tables and recreate)."""
    if typer.confirm("This will delete all data. Are you sure?"):
        logger.info("Resetting database...")
        asyncio.run(migrations.reset_database())

        if reindex:
            # Import and run sync
            from basic_memory.cli.commands.sync import sync

            logger.info("Rebuilding search index from filesystem...")
            asyncio.run(sync())  # pyright: ignore
