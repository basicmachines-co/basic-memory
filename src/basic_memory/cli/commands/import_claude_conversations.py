"""Import command for basic-memory CLI to import chat data from conversations2.json format."""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Annotated

import logfire
import typer
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from basic_memory.cli.app import claude_app
from basic_memory.config import config
from basic_memory.markdown import EntityParser, MarkdownProcessor
from basic_memory.markdown.schemas import EntityMarkdown, EntityFrontmatter

console = Console()


def clean_filename(text: str) -> str:
    """Convert text to safe filename."""
    # Remove invalid characters and convert spaces
    clean = "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")
    return clean


def format_timestamp(ts: str) -> str:
    """Format ISO timestamp for display."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def format_chat_markdown(
    name: str, messages: List[Dict[str, Any]], created_at: str, modified_at: str, permalink: str
) -> str:
    """Format chat as clean markdown."""

    # Start with frontmatter and title
    lines = [
        f"# {name}\n",
    ]

    # Add messages
    for msg in messages:
        # Format timestamp
        ts = format_timestamp(msg["created_at"])

        # Add message header
        lines.append(f"### {msg['sender'].title()} ({ts})")

        # Handle message content
        content = msg.get("text", "")
        if msg.get("content"):
            content = " ".join(c.get("text", "") for c in msg["content"])
        lines.append(content)

        # Handle attachments
        attachments = msg.get("attachments", [])
        for attachment in attachments:
            if "file_name" in attachment:
                lines.append(f"\n**Attachment: {attachment['file_name']}**")
                if "extracted_content" in attachment:
                    lines.append("```")
                    lines.append(attachment["extracted_content"])
                    lines.append("```")

        # Add spacing between messages
        lines.append("")

    return "\n".join(lines)


def format_chat_content(
    base_path: Path, name: str, messages: List[Dict[str, Any]], created_at: str, modified_at: str
) -> EntityMarkdown:
    """Convert chat messages to Basic Memory entity format."""

    # Generate permalink
    date_prefix = datetime.fromisoformat(created_at.replace("Z", "+00:00")).strftime("%Y%m%d")
    clean_title = clean_filename(name)
    permalink = f"{base_path}/{date_prefix}-{clean_title}"

    # Format content
    content = format_chat_markdown(
        name=name,
        messages=messages,
        created_at=created_at,
        modified_at=modified_at,
        permalink=permalink,
    )

    # Create entity
    entity = EntityMarkdown(
        frontmatter=EntityFrontmatter(
            metadata={
                "type": "conversation",
                "title": name,
                "created": created_at,
                "modified": modified_at,
                "permalink": permalink,
            }
        ),
        content=content,
    )

    return entity


async def process_conversations_json(
    json_path: Path, base_path: Path, markdown_processor: MarkdownProcessor
) -> Dict[str, int]:
    """Import chat data from conversations2.json format."""

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        read_task = progress.add_task("Reading chat data...", total=None)

        # Read chat data - handle array of arrays format
        data = json.loads(json_path.read_text())
        conversations = [chat for chat in data]
        progress.update(read_task, total=len(conversations))

        # Process each conversation
        messages_imported = 0
        chats_imported = 0

        for chat in conversations:
            # Convert to entity
            entity = format_chat_content(
                base_path=base_path,
                name=chat["name"],
                messages=chat["chat_messages"],
                created_at=chat["created_at"],
                modified_at=chat["updated_at"],
            )

            # Write file
            file_path = Path(f"{entity.frontmatter.metadata['permalink']}.md")
            await markdown_processor.write_file(file_path, entity)

            chats_imported += 1
            messages_imported += len(chat["chat_messages"])
            progress.update(read_task, advance=1)

    return {"conversations": chats_imported, "messages": messages_imported}


async def get_markdown_processor() -> MarkdownProcessor:
    """Get MarkdownProcessor instance."""
    entity_parser = EntityParser(config.home)
    return MarkdownProcessor(entity_parser)


@claude_app.command(name="conversations", help="Import chat conversations from Claude.ai.")
@logfire.instrument(extract_args=False)
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

        # Process the file
        base_path = config.home / folder
        console.print(f"\nImporting chats from {conversations_json}...writing to {base_path}")
        results = asyncio.run(
            process_conversations_json(conversations_json, base_path, markdown_processor)
        )

        # Show results
        console.print(
            Panel(
                f"[green]Import complete![/green]\n\n"
                f"Imported {results['conversations']} conversations\n"
                f"Containing {results['messages']} messages",
                expand=False,
            )
        )

        console.print("\nRun 'basic-memory sync' to index the new files.")

    except Exception as e:
        logger.error("Import failed")
        typer.echo(f"Error during import: {e}", err=True)
        raise typer.Exit(1)
