"""Database management commands."""

import logfire
import typer
from loguru import logger

from basic_memory.alembic import migrations
from basic_memory.cli.app import app


@logfire.instrument()
@app.command()
def reset(
    reindex: bool = typer.Option(False, "--reindex", help="Rebuild db index from filesystem"),
):  # pragma: no cover
    """Reset database (drop all tables and recreate)."""
    if typer.confirm("This will delete all data in your db. Are you sure?"):
        logger.info("Resetting database...")
        migrations.reset_database()

        if reindex:
            # Import and run sync
            from basic_memory.cli.commands.sync import sync

            logger.info("Rebuilding search index from filesystem...")
            sync(watch=False)  # pyright: ignore
