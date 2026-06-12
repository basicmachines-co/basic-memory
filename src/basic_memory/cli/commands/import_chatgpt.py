"""Import command for ChatGPT conversations."""

# PEP 563 lazy annotations keep heavy importer types out of module import (#886).
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Tuple

import typer
from basic_memory.cli.app import import_app
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


@import_app.command(name="chatgpt", help="Import conversations from ChatGPT JSON export.")
def import_chatgpt(
    conversations_json: Annotated[
        Path, typer.Argument(help="Path to ChatGPT conversations.json file")
    ] = Path("conversations.json"),
    folder: Annotated[
        str, typer.Option(help="The folder to place the files in.")
    ] = "conversations",
):
    """Import chat conversations from ChatGPT JSON format.

    This command will:
    1. Read the complex tree structure of messages
    2. Convert them to linear markdown conversations
    3. Save as clean, readable markdown files

    After importing, run 'bm reindex --search' to index the new files.
    """

    try:
        if not conversations_json.exists():  # pragma: no cover
            typer.echo(f"Error: File not found: {conversations_json}", err=True)
            raise typer.Exit(1)

        # Get importer dependencies
        markdown_processor, file_service = run_with_cleanup(get_importer_dependencies())
        config = get_project_config()
        # Process the file
        base_path = config.home / folder
        console.print(f"\nImporting chats from {conversations_json}...writing to {base_path}")

        # Create importer and run import
        # Deferred: importer stack loads at import-command run time only (#886).
        from basic_memory.importers import ChatGPTImporter

        importer = ChatGPTImporter(
            config.home, markdown_processor, file_service, project_name=config.name
        )
        with conversations_json.open("r", encoding="utf-8") as file:
            json_data = json.load(file)
            result = run_with_cleanup(importer.import_data(json_data, folder))

        if not result.success:  # pragma: no cover
            typer.echo(f"Error during import: {result.error_message}", err=True)
            raise typer.Exit(1)

        # Show results
        console.print(
            Panel(
                f"[green]Import complete![/green]\n\n"
                f"Imported {result.conversations} conversations\n"
                f"Containing {result.messages} messages",
                expand=False,
            )
        )

        console.print("\nRun 'bm reindex --search' to index the new files.")

    except Exception as e:
        logger.error("Import failed")
        typer.echo(f"Error during import: {e}", err=True)
        raise typer.Exit(1)
