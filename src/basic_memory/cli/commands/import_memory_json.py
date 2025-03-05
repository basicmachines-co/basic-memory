"""Import command for basic-memory CLI to import from JSON memory format."""

import asyncio
import json
from pathlib import Path
from typing import Dict, Any, List, Annotated

import logfire
import typer
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from basic_memory.cli.app import import_app
from basic_memory.config import config
from basic_memory.markdown import EntityParser, MarkdownProcessor
from basic_memory.markdown.schemas import EntityMarkdown, EntityFrontmatter, Observation, Relation

console = Console()


async def process_memory_json(
    json_path: Path, base_path: Path, markdown_processor: MarkdownProcessor
):
    """Import entities from memory.json using markdown processor."""

    # First pass - collect all relations by source entity
    entity_relations: Dict[str, List[Relation]] = {}
    entities: Dict[str, Dict[str, Any]] = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        read_task = progress.add_task("Reading memory.json...", total=None)

        # First pass - collect entities and relations
        with open(json_path) as f:
            lines = f.readlines()
            progress.update(read_task, total=len(lines))

            for line in lines:
                data = json.loads(line)
                if data["type"] == "entity":
                    entities[data["name"]] = data
                elif data["type"] == "relation":
                    # Store relation with its source entity
                    source = data.get("from") or data.get("from_id")
                    if source not in entity_relations:
                        entity_relations[source] = []
                    entity_relations[source].append(
                        Relation(
                            type=data.get("relationType") or data.get("relation_type"),
                            target=data.get("to") or data.get("to_id"),
                        )
                    )
                progress.update(read_task, advance=1)

        # Second pass - create and write entities
        write_task = progress.add_task("Creating entities...", total=len(entities))

        entities_created = 0
        for name, entity_data in entities.items():
            entity = EntityMarkdown(
                frontmatter=EntityFrontmatter(
                    metadata={
                        "type": entity_data["entityType"],
                        "title": name,
                        "permalink": f"{entity_data['entityType']}/{name}",
                    }
                ),
                content=f"# {name}\n",
                observations=[Observation(content=obs) for obs in entity_data["observations"]],
                relations=entity_relations.get(
                    name, []
                ),  # Add any relations where this entity is the source
            )

            # Let markdown processor handle writing
            file_path = base_path / f"{entity_data['entityType']}/{name}.md"
            await markdown_processor.write_file(file_path, entity)
            entities_created += 1
            progress.update(write_task, advance=1)

    return {
        "entities": entities_created,
        "relations": sum(len(rels) for rels in entity_relations.values()),
    }


async def get_markdown_processor() -> MarkdownProcessor:
    """Get MarkdownProcessor instance."""
    entity_parser = EntityParser(config.home)
    return MarkdownProcessor(entity_parser)


@import_app.command()
@logfire.instrument(extract_args=False)
def memory_json(
    json_path: Annotated[Path, typer.Argument(..., help="Path to memory.json file")] = Path(
        "memory.json"
    ),
):
    """Import entities and relations from a memory.json file.

    This command will:
    1. Read entities and relations from the JSON file
    2. Create markdown files for each entity
    3. Include outgoing relations in each entity's markdown

    After importing, run 'basic-memory sync' to index the new files.
    """

    if not json_path.exists():
        typer.echo(f"Error: File not found: {json_path}", err=True)
        raise typer.Exit(1)

    try:
        # Get markdown processor
        markdown_processor = asyncio.run(get_markdown_processor())

        # Process the file
        base_path = config.home
        console.print(f"\nImporting from {json_path}...writing to {base_path}")
        results = asyncio.run(process_memory_json(json_path, base_path, markdown_processor))

        # Show results
        console.print(
            Panel(
                f"[green]Import complete![/green]\n\n"
                f"Created {results['entities']} entities\n"
                f"Added {results['relations']} relations",
                expand=False,
            )
        )

        console.print("\nRun 'basic-memory sync' to index the new files.")

    except Exception as e:
        logger.error("Import failed")
        typer.echo(f"Error during import: {e}", err=True)
        raise typer.Exit(1)
