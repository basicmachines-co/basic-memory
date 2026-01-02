"""Import command for basic-memory CLI to import from JSON memory format."""

import asyncio
import json
from pathlib import Path
from typing import Annotated, Tuple

import typer
from basic_memory.cli.app import import_app
from basic_memory.config import ConfigManager, get_project_config
from basic_memory.importers.memory_json_importer import MemoryJsonImporter
from basic_memory.markdown import EntityParser, MarkdownProcessor
from basic_memory.services.file_service import FileService
from loguru import logger
from rich.console import Console
from rich.panel import Panel

console = Console()


async def get_importer_dependencies() -> Tuple[MarkdownProcessor, FileService]:
    """Get MarkdownProcessor and FileService instances for importers."""
    config = get_project_config()
    app_config = ConfigManager().config
    entity_parser = EntityParser(config.home)
    markdown_processor = MarkdownProcessor(entity_parser, app_config=app_config)
    file_service = FileService(config.home, markdown_processor, app_config=app_config)
    return markdown_processor, file_service


@import_app.command()
def memory_json(
    json_path: Annotated[Path, typer.Argument(..., help="Path to memory.json file")] = Path(
        "memory.json"
    ),
    destination_folder: Annotated[
        str, typer.Option(help="Optional destination folder within the project")
    ] = "",
):
    """Import entities and relations from a memory.json file.

    This command will:
    1. Read entities and relations from the JSON file
    2. Create markdown files for each entity
    3. Include outgoing relations in each entity's markdown
    """

    if not json_path.exists():
        typer.echo(f"Error: File not found: {json_path}", err=True)
        raise typer.Exit(1)

    config = get_project_config()
    try:
        # Get importer dependencies
        markdown_processor, file_service = asyncio.run(get_importer_dependencies())

        # Create the importer
        importer = MemoryJsonImporter(config.home, markdown_processor, file_service)

        # Process the file
        base_path = config.home if not destination_folder else config.home / destination_folder
        console.print(f"\nImporting from {json_path}...writing to {base_path}")

        # Run the import for json log format
        file_data = []
        with json_path.open("r", encoding="utf-8") as file:
            for line in file:
                json_data = json.loads(line)
                file_data.append(json_data)
        result = asyncio.run(importer.import_data(file_data, destination_folder))

        if not result.success:  # pragma: no cover
            typer.echo(f"Error during import: {result.error_message}", err=True)
            raise typer.Exit(1)

        # Show results
        console.print(
            Panel(
                f"[green]Import complete![/green]\n\n"
                f"Created {result.entities} entities\n"
                f"Added {result.relations} relations\n"
                f"Skipped {result.skipped_entities} entities\n",
                expand=False,
            )
        )

    except Exception as e:
        logger.error("Import failed")
        typer.echo(f"Error during import: {e}", err=True)
        raise typer.Exit(1)
