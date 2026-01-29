#!/usr/bin/env python3
"""Rebuild search index script.

This script clears and rebuilds the search index used by search and build_context
without affecting the underlying entities or files.

Usage:
    python tmp/rebuild_search_index.py [--project PROJECT_NAME]

What it does:
    1. Drops the search_index table (FTS5 virtual table for SQLite, regular table for Postgres)
    2. Recreates the search_index table
    3. Re-indexes all entities with their observations and relations

This is equivalent to calling POST /search/reindex via the API.
"""

import asyncio
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from loguru import logger
from rich.console import Console

from basic_memory import db
from basic_memory.config import ConfigManager
from basic_memory.markdown import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.repository import EntityRepository, ProjectRepository
from basic_memory.repository.search_repository import create_search_repository
from basic_memory.services.file_service import FileService
from basic_memory.services.search_service import SearchService

console = Console()


async def rebuild_search_index(project_name: str | None = None):
    """Rebuild the search index for all or a specific project."""
    config_manager = ConfigManager()
    app_config = config_manager.config

    console.print("[bold]Rebuilding search index...[/bold]")

    # Get database session
    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path,
        db_type=db.DatabaseType.FILESYSTEM,
    )

    try:
        # Get projects to reindex
        project_repository = ProjectRepository(session_maker)
        projects = await project_repository.get_active_projects()

        if project_name:
            projects = [p for p in projects if p.name == project_name]
            if not projects:
                console.print(f"[red]Project '{project_name}' not found[/red]")
                return

        for project in projects:
            console.print(f"\n[cyan]Reindexing project: {project.name}[/cyan]")
            logger.info(f"Reindexing project: {project.name}")

            # Create dependencies for this project
            project_home = Path(project.path)
            entity_parser = EntityParser(project_home)
            markdown_processor = MarkdownProcessor(entity_parser, app_config=app_config)
            file_service = FileService(project_home, markdown_processor, app_config=app_config)

            entity_repository = EntityRepository(session_maker, project_id=project.id)
            search_repository = create_search_repository(session_maker, project_id=project.id)

            search_service = SearchService(
                search_repository=search_repository,
                entity_repository=entity_repository,
                file_service=file_service,
            )

            # Rebuild index
            await search_service.reindex_all()

            # Count indexed items
            entity_count = len(await entity_repository.find_all())
            console.print(f"  [green]Indexed {entity_count} entities[/green]")

        console.print("\n[bold green]Search index rebuild complete![/bold green]")

    finally:
        await db.shutdown_db()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Rebuild search index")
    parser.add_argument(
        "--project",
        "-p",
        help="Project name to reindex (default: all projects)",
        default=None,
    )
    args = parser.parse_args()

    asyncio.run(rebuild_search_index(args.project))


if __name__ == "__main__":
    main()
