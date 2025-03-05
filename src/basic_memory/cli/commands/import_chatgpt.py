"""Import command for ChatGPT conversations."""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Annotated, Set, Optional

import logfire
import typer
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from basic_memory.cli.app import import_app
from basic_memory.config import config
from basic_memory.markdown import EntityParser, MarkdownProcessor
from basic_memory.markdown.schemas import EntityMarkdown, EntityFrontmatter

console = Console()


def clean_filename(text: str) -> str:
    """Convert text to safe filename."""
    clean = "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")
    return clean


def format_timestamp(ts: float) -> str:
    """Format Unix timestamp for display."""
    dt = datetime.fromtimestamp(ts)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def get_message_content(message: Dict[str, Any]) -> str:
    """Extract clean message content."""
    if not message or "content" not in message:
        return ""  # pragma: no cover

    content = message["content"]
    if content.get("content_type") == "text":
        return "\n".join(content.get("parts", []))
    elif content.get("content_type") == "code":
        return f"```{content.get('language', '')}\n{content.get('text', '')}\n```"
    return ""  # pragma: no cover


def traverse_messages(
    mapping: Dict[str, Any], root_id: Optional[str], seen: Set[str]
) -> List[Dict[str, Any]]:
    """Traverse message tree and return messages in order."""
    messages = []
    node = mapping.get(root_id) if root_id else None

    while node:
        if node["id"] not in seen and node.get("message"):
            seen.add(node["id"])
            messages.append(node["message"])

        # Follow children
        children = node.get("children", [])
        for child_id in children:
            child_msgs = traverse_messages(mapping, child_id, seen)
            messages.extend(child_msgs)

        break  # Don't follow siblings

    return messages


def format_chat_markdown(
    title: str,
    mapping: Dict[str, Any],
    root_id: Optional[str],
    created_at: float,
    modified_at: float,
) -> str:
    """Format chat as clean markdown."""

    # Start with title
    lines = [f"# {title}\n"]

    # Traverse message tree
    seen_msgs = set()
    messages = traverse_messages(mapping, root_id, seen_msgs)

    # Format each message
    for msg in messages:
        # Skip hidden messages
        if msg.get("metadata", {}).get("is_visually_hidden_from_conversation"):
            continue

        # Get author and timestamp
        author = msg["author"]["role"].title()
        ts = format_timestamp(msg["create_time"]) if msg.get("create_time") else ""

        # Add message header
        lines.append(f"### {author} ({ts})")

        # Add message content
        content = get_message_content(msg)
        if content:
            lines.append(content)

        # Add spacing
        lines.append("")

    return "\n".join(lines)


def format_chat_content(folder: str, conversation: Dict[str, Any]) -> EntityMarkdown:
    """Convert chat conversation to Basic Memory entity."""

    # Extract timestamps
    created_at = conversation["create_time"]
    modified_at = conversation["update_time"]

    root_id = None
    # Find root message
    for node_id, node in conversation["mapping"].items():
        if node.get("parent") is None:
            root_id = node_id
            break

    # Generate permalink
    date_prefix = datetime.fromtimestamp(created_at).strftime("%Y%m%d")
    clean_title = clean_filename(conversation["title"])

    # Format content
    content = format_chat_markdown(
        title=conversation["title"],
        mapping=conversation["mapping"],
        root_id=root_id,
        created_at=created_at,
        modified_at=modified_at,
    )

    # Create entity
    entity = EntityMarkdown(
        frontmatter=EntityFrontmatter(
            metadata={
                "type": "conversation",
                "title": conversation["title"],
                "created": format_timestamp(created_at),
                "modified": format_timestamp(modified_at),
                "permalink": f"{folder}/{date_prefix}-{clean_title}",
            }
        ),
        content=content,
    )

    return entity


async def process_chatgpt_json(
    json_path: Path, folder: str, markdown_processor: MarkdownProcessor
) -> Dict[str, int]:
    """Import conversations from ChatGPT JSON format."""

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        read_task = progress.add_task("Reading chat data...", total=None)

        # Read conversations
        conversations = json.loads(json_path.read_text())
        progress.update(read_task, total=len(conversations))

        # Process each conversation
        messages_imported = 0
        chats_imported = 0

        for chat in conversations:
            # Convert to entity
            entity = format_chat_content(folder, chat)

            # Write file
            file_path = config.home / f"{entity.frontmatter.metadata['permalink']}.md"
            # logger.info(f"Writing file: {file_path.absolute()}")
            await markdown_processor.write_file(file_path, entity)

            # Count messages
            msg_count = sum(
                1
                for node in chat["mapping"].values()
                if node.get("message")
                and not node.get("message", {})
                .get("metadata", {})
                .get("is_visually_hidden_from_conversation")
            )

            chats_imported += 1
            messages_imported += msg_count
            progress.update(read_task, advance=1)

    return {"conversations": chats_imported, "messages": messages_imported}


async def get_markdown_processor() -> MarkdownProcessor:
    """Get MarkdownProcessor instance."""
    entity_parser = EntityParser(config.home)
    return MarkdownProcessor(entity_parser)


@import_app.command(name="chatgpt", help="Import conversations from ChatGPT JSON export.")
@logfire.instrument(extract_args=False)
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

    After importing, run 'basic-memory sync' to index the new files.
    """

    try:
        if conversations_json:
            if not conversations_json.exists():
                typer.echo(f"Error: File not found: {conversations_json}", err=True)
                raise typer.Exit(1)

            # Get markdown processor
            markdown_processor = asyncio.run(get_markdown_processor())

            # Process the file
            base_path = config.home / folder
            console.print(f"\nImporting chats from {conversations_json}...writing to {base_path}")
            results = asyncio.run(
                process_chatgpt_json(conversations_json, folder, markdown_processor)
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
