"""Database management commands."""

import asyncio
from pathlib import Path

import typer
from loguru import logger

from basic_memory import db
from basic_memory.cli.app import app
from basic_memory.config import app_config, config_manager


@app.command()
def reset(
    reindex: bool = typer.Option(False, "--reindex", help="Rebuild db index from filesystem"),
):  # pragma: no cover
    """Reset database (drop all tables and recreate)."""
    if typer.confirm("This will delete all data in your db. Are you sure?"):
        logger.info("Resetting database...")
        # Get database path
        db_path = app_config.app_database_path

        # Delete the database file if it exists
        if db_path.exists():
            db_path.unlink()
            logger.info(f"Database file deleted: {db_path}")

        # Reset project configuration
        config_manager.config.projects = {"main": str(Path.home() / "basic-memory")}
        config_manager.config.default_project = "main"
        config_manager.save_config(config_manager.config)
        logger.info("Project configuration reset to default")

        # Create a new empty database
        asyncio.run(db.run_migrations(app_config))
        logger.info("Database reset complete")

        if reindex:
            # Import and run sync
            from basic_memory.cli.commands.sync import sync

            logger.info("Rebuilding search index from filesystem...")
            sync(watch=False)  # pyright: ignore
