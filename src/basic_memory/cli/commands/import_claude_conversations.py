"""Import command for basic-memory CLI to import chat data from conversations2.json format."""

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from basic_memory.cli.app import claude_app
from basic_memory.config import config
from basic_memory.importers.claude_conversations_importer import ClaudeConversationsImporter
from basic_memory.markdown import EntityParser, MarkdownProcessor
from loguru import logger
from rich.console import Console
from rich.panel import Panel

console = Console()


async def get_markdown_processor() -> MarkdownProcessor:
    """Get MarkdownProcessor instance."""
    entity_parser = EntityParser(config.home)
    return MarkdownProcessor(entity_parser)


@claude_app.command(name="conversations", help="Import chat conversations from Claude.ai.")
def import_claude(
    conversations_json: Annotated[
        Path, typer.Argument(..., help="Path to conversations.json file")
    ] = Path("conversations.json"),
    folder: Annotated[
        str, typer.Option(help="The folder to place the files in.")
    ] = "conversations",
):
    """Import chat conversations from conversations2.json format.

    This command will:
    1. Read chat data and nested messages
    2. Create markdown files for each conversation
    3. Format content in clean, readable markdown

    After importing, run 'basic-memory sync' to index the new files.
    """

    try:
        if not conversations_json.exists():
            typer.echo(f"Error: File not found: {conversations_json}", err=True)
            raise typer.Exit(1)

        # Get markdown processor
        markdown_processor = asyncio.run(get_markdown_processor())

        # Create the importer
        importer = ClaudeConversationsImporter(config.home, markdown_processor)

        # Process the file
        base_path = config.home / folder
        console.print(f"\nImporting chats from {conversations_json}...writing to {base_path}")

        # Run the import
        with conversations_json.open("r", encoding="utf-8") as file:
            json_data = json.load(file)
            result = asyncio.run(importer.import_data(json_data, folder))

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

        console.print("\nRun 'basic-memory sync' to index the new files.")

    except Exception as e:
        logger.error("Import failed")
        typer.echo(f"Error during import: {e}", err=True)
        raise typer.Exit(1)
