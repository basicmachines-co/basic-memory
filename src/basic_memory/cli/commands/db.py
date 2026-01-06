"""Database management commands."""

import asyncio
from pathlib import Path

import typer
from loguru import logger
from rich.console import Console
from sqlalchemy.exc import OperationalError

from basic_memory import db
from basic_memory.cli.app import app
from basic_memory.config import ConfigManager
from basic_memory.repository import ProjectRepository
from basic_memory.services.initialization import reconcile_projects_with_config
from basic_memory.sync.sync_service import get_sync_service

console = Console()


async def _reindex_projects(app_config):
    """Reindex all projects in a single async context.

    This ensures all database operations use the same event loop,
    and proper cleanup happens when the function completes.
    """
    try:
        await reconcile_projects_with_config(app_config)

        # Get database session (migrations already run if needed)
        _, session_maker = await db.get_or_create_db(
            db_path=app_config.database_path,
            db_type=db.DatabaseType.FILESYSTEM,
        )
        project_repository = ProjectRepository(session_maker)
        projects = await project_repository.get_active_projects()

        for project in projects:
            console.print(f"  Indexing [cyan]{project.name}[/cyan]...")
            logger.info(f"Starting sync for project: {project.name}")
            sync_service = await get_sync_service(project)
            sync_dir = Path(project.path)
            await sync_service.sync(sync_dir, project_name=project.name)
            logger.info(f"Sync completed for project: {project.name}")
    finally:
        # Clean up database connections before event loop closes
        await db.shutdown_db()


@app.command()
def reset(
    reindex: bool = typer.Option(False, "--reindex", help="Rebuild db index from filesystem"),
):  # pragma: no cover
    """Reset database (drop all tables and recreate)."""
    console.print(
        "[yellow]Note:[/yellow] This only deletes the index database. "
        "Your markdown note files will not be affected.\n"
        "Use [green]bm reset --reindex[/green] to automatically rebuild the index afterward."
    )
    if typer.confirm("Reset the database index?"):
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

        # Create a new empty database (preserves project configuration)
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
        console.print("[green]Database reset complete[/green]")

        if reindex:
            projects = list(app_config.projects)
            if not projects:
                console.print("[yellow]No projects configured. Skipping reindex.[/yellow]")
            else:
                console.print(f"Rebuilding search index for {len(projects)} project(s)...")
                asyncio.run(_reindex_projects(app_config))
                console.print("[green]Reindex complete[/green]")
