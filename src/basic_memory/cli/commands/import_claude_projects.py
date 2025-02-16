"""Import command for basic-memory CLI to import project data from Claude.ai."""

import asyncio
import json
from pathlib import Path
from typing import Dict, Any, Annotated, Optional

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
    clean = "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")
    return clean


def format_project_markdown(project: Dict[str, Any], doc: Dict[str, Any]) -> EntityMarkdown:
    """Format a project document as a Basic Memory entity."""

    # Extract timestamps
    created_at = doc.get("created_at") or project["created_at"]
    modified_at = project["updated_at"]

    # Generate clean names for organization
    project_dir = clean_filename(project["name"])
    doc_file = clean_filename(doc["filename"])

    # Create entity
    entity = EntityMarkdown(
        frontmatter=EntityFrontmatter(
            metadata={
                "type": "project_doc",
                "title": doc["filename"],
                "created": created_at,
                "modified": modified_at,
                "permalink": f"{project_dir}/docs/{doc_file}",
                "project_name": project["name"],
                "project_uuid": project["uuid"],
                "doc_uuid": doc["uuid"],
            }
        ),
        content=doc["content"],
    )

    return entity


def format_prompt_markdown(project: Dict[str, Any]) -> Optional[EntityMarkdown]:
    """Format project prompt template as a Basic Memory entity."""

    if not project.get("prompt_template"):
        return None

    # Extract timestamps
    created_at = project["created_at"]
    modified_at = project["updated_at"]

    # Generate clean project directory name
    project_dir = clean_filename(project["name"])

    # Create entity
    entity = EntityMarkdown(
        frontmatter=EntityFrontmatter(
            metadata={
                "type": "prompt_template",
                "title": f"Prompt Template: {project['name']}",
                "created": created_at,
                "modified": modified_at,
                "permalink": f"{project_dir}/prompt-template",
                "project_name": project["name"],
                "project_uuid": project["uuid"],
            }
        ),
        content=f"# Prompt Template: {project['name']}\n\n{project['prompt_template']}",
    )

    return entity


async def process_projects_json(
    json_path: Path, base_path: Path, markdown_processor: MarkdownProcessor
) -> Dict[str, int]:
    """Import project data from Claude.ai projects.json format."""

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        read_task = progress.add_task("Reading project data...", total=None)

        # Read project data
        data = json.loads(json_path.read_text())
        progress.update(read_task, total=len(data))

        # Track import counts
        docs_imported = 0
        prompts_imported = 0

        # Process each project
        for project in data:
            project_dir = clean_filename(project["name"])

            # Create project directories
            docs_dir = base_path / project_dir / "docs"
            docs_dir.mkdir(parents=True, exist_ok=True)

            # Import prompt template if it exists
            if prompt_entity := format_prompt_markdown(project):
                file_path = base_path / f"{prompt_entity.frontmatter.metadata['permalink']}.md"
                await markdown_processor.write_file(file_path, prompt_entity)
                prompts_imported += 1

            # Import project documents
            for doc in project.get("docs", []):
                entity = format_project_markdown(project, doc)
                file_path = base_path / f"{entity.frontmatter.metadata['permalink']}.md"
                await markdown_processor.write_file(file_path, entity)
                docs_imported += 1

            progress.update(read_task, advance=1)

    return {"documents": docs_imported, "prompts": prompts_imported}


async def get_markdown_processor() -> MarkdownProcessor:
    """Get MarkdownProcessor instance."""
    entity_parser = EntityParser(config.home)
    return MarkdownProcessor(entity_parser)


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

    After importing, run 'basic-memory sync' to index the new files.
    """

    try:
        if projects_json:
            if not projects_json.exists():
                typer.echo(f"Error: File not found: {projects_json}", err=True)
                raise typer.Exit(1)

            # Get markdown processor
            markdown_processor = asyncio.run(get_markdown_processor())

            # Process the file
            base_path = config.home / base_folder if base_folder else config.home
            console.print(f"\nImporting projects from {projects_json}...writing to {base_path}")
            results = asyncio.run(
                process_projects_json(projects_json, base_path, markdown_processor)
            )

            # Show results
            console.print(
                Panel(
                    f"[green]Import complete![/green]\n\n"
                    f"Imported {results['documents']} project documents\n"
                    f"Imported {results['prompts']} prompt templates",
                    expand=False,
                )
            )

        console.print("\nRun 'basic-memory sync' to index the new files.")

    except Exception as e:
        logger.exception("Import failed")
        typer.echo(f"Error during import: {e}", err=True)
        raise typer.Exit(1)
