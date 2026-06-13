"""Import command for basic-memory CLI to import project data from Claude.ai."""

# PEP 563 lazy annotations keep heavy importer types out of module import (#886).
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Tuple

import typer
from basic_memory.cli.app import claude_app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.config import ConfigManager, get_project_config
from loguru import logger
from rich.console import Console
from rich.panel import Panel

if TYPE_CHECKING:
    from basic_memory.markdown import MarkdownProcessor
    from basic_memory.services.file_service import FileService

console = Console()


async def get_importer_dependencies() -> Tuple[MarkdownProcessor, FileService]:
    """Get MarkdownProcessor and FileService instances for importers."""
    # Deferred: the markdown/file-service stack pulls SQLAlchemy and must load
    # only when an import actually runs, not on every CLI start (#886).
    from basic_memory.markdown import EntityParser, MarkdownProcessor
    from basic_memory.services.file_service import FileService

    config = get_project_config()
    app_config = ConfigManager().config
    entity_parser = EntityParser(config.home)
    markdown_processor = MarkdownProcessor(entity_parser, app_config=app_config)
    file_service = FileService(config.home, markdown_processor, app_config=app_config)
    return markdown_processor, file_service


@claude_app.command(name="projects", help="Import projects from Claude.ai.")
def import_projects(
    projects_json: Annotated[Path, typer.Argument(..., help="Path to projects.json file")] = Path(
        "projects.json"
    ),
    base_folder: Annotated[
        str, typer.Option(help="The base folder to place project files in.")
    ] = "projects",
):
    """Import project data from Claude.ai.

    This command will:
    1. Create a directory for each project
    2. Store docs in a docs/ subdirectory
    3. Place prompt template in project root

    After importing, run 'bm reindex --search' to index the new files.
    """
    config = get_project_config()
    try:
        if not projects_json.exists():
            typer.echo(f"Error: File not found: {projects_json}", err=True)
            raise typer.Exit(1)

        # Get importer dependencies
        markdown_processor, file_service = run_with_cleanup(get_importer_dependencies())

        # Create the importer
        # Deferred: importer stack loads at import-command run time only (#886).
        from basic_memory.importers.claude_projects_importer import ClaudeProjectsImporter

        importer = ClaudeProjectsImporter(
            config.home, markdown_processor, file_service, project_name=config.name
        )

        # Process the file
        base_path = config.home / base_folder if base_folder else config.home
        console.print(f"\nImporting projects from {projects_json}...writing to {base_path}")

        # Run the import
        with projects_json.open("r", encoding="utf-8") as file:
            json_data = json.load(file)
            result = run_with_cleanup(importer.import_data(json_data, base_folder))

        if not result.success:  # pragma: no cover
            typer.echo(f"Error during import: {result.error_message}", err=True)
            raise typer.Exit(1)

        # Show results
        console.print(
            Panel(
                f"[green]Import complete![/green]\n\n"
                f"Imported {result.documents} project documents\n"
                f"Imported {result.prompts} prompt templates",
                expand=False,
            )
        )

        console.print("\nRun 'bm reindex --search' to index the new files.")

    except Exception as e:
        logger.error("Import failed")
        typer.echo(f"Error during import: {e}", err=True)
        raise typer.Exit(1)
