"""Database management commands."""

from pathlib import Path

import typer
from loguru import logger
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from sqlalchemy.exc import OperationalError

from basic_memory import db
from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
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
            run_with_cleanup(db.run_migrations(app_config))
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
                # Note: _reindex_projects has its own cleanup, but run_with_cleanup
                # ensures db.shutdown_db() is called even if _reindex_projects changes
                run_with_cleanup(_reindex_projects(app_config))
                console.print("[green]Reindex complete[/green]")


@app.command()
def reindex(
    embeddings: bool = typer.Option(
        False, "--embeddings", "-e", help="Rebuild vector embeddings (requires semantic search)"
    ),
    search: bool = typer.Option(False, "--search", "-s", help="Rebuild full-text search index"),
    project: str = typer.Option(
        None, "--project", "-p", help="Reindex a specific project (default: all)"
    ),
):  # pragma: no cover
    """Rebuild search indexes and/or vector embeddings without dropping the database.

    By default rebuilds everything (search + embeddings if semantic is enabled).
    Use --search or --embeddings to rebuild only one.

    Examples:
        bm reindex                  # Rebuild everything
        bm reindex --embeddings     # Only rebuild vector embeddings
        bm reindex --search         # Only rebuild FTS index
        bm reindex -p claw          # Reindex only the 'claw' project
    """
    # If neither flag is set, do both
    if not embeddings and not search:
        embeddings = True
        search = True

    config_manager = ConfigManager()
    app_config = config_manager.config

    if embeddings and not app_config.semantic_search_enabled:
        console.print(
            "[yellow]Semantic search is not enabled.[/yellow] "
            "Set [cyan]semantic_search_enabled: true[/cyan] in config to use embeddings."
        )
        embeddings = False
        if not search:
            raise typer.Exit(0)

    run_with_cleanup(_reindex(app_config, search=search, embeddings=embeddings, project=project))


async def _reindex(app_config, search: bool, embeddings: bool, project: str | None):
    """Run reindex operations."""
    from basic_memory.repository import EntityRepository
    from basic_memory.repository.search_repository import create_search_repository
    from basic_memory.services.search_service import SearchService
    from basic_memory.services.file_service import FileService
    from basic_memory.markdown.markdown_processor import MarkdownProcessor
    from basic_memory.markdown.entity_parser import EntityParser

    try:
        await reconcile_projects_with_config(app_config)

        _, session_maker = await db.get_or_create_db(
            db_path=app_config.database_path,
            db_type=db.DatabaseType.FILESYSTEM,
        )
        project_repository = ProjectRepository(session_maker)
        projects = await project_repository.get_active_projects()

        if project:
            projects = [p for p in projects if p.name == project]
            if not projects:
                console.print(f"[red]Project '{project}' not found.[/red]")
                raise typer.Exit(1)

        for proj in projects:
            console.print(f"\n[bold]Project: [cyan]{proj.name}[/cyan][/bold]")

            if search:
                console.print("  Rebuilding full-text search index...")
                sync_service = await get_sync_service(proj)
                sync_dir = Path(proj.path)
                await sync_service.sync(sync_dir, project_name=proj.name)
                console.print("  [green]✓[/green] Full-text search index rebuilt")

            if embeddings:
                console.print("  Building vector embeddings...")
                entity_repository = EntityRepository(session_maker, project_id=proj.id)
                search_repository = create_search_repository(
                    session_maker, project_id=proj.id, app_config=app_config
                )
                project_path = Path(proj.path)
                entity_parser = EntityParser(project_path)
                markdown_processor = MarkdownProcessor(entity_parser, app_config=app_config)
                file_service = FileService(project_path, markdown_processor, app_config=app_config)
                search_service = SearchService(search_repository, entity_repository, file_service)

                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    console=console,
                ) as progress:
                    task = progress.add_task("  Embedding entities...", total=None)

                    def on_progress(entity_id, index, total):
                        progress.update(task, total=total, completed=index)

                    stats = await search_service.reindex_vectors(progress_callback=on_progress)
                    progress.update(task, completed=stats["total_entities"])

                console.print(
                    f"  [green]✓[/green] Embeddings complete: "
                    f"{stats['embedded']} entities embedded, "
                    f"{stats['skipped']} skipped, "
                    f"{stats['errors']} errors"
                )

        console.print("\n[green]Reindex complete![/green]")
    finally:
        await db.shutdown_db()
