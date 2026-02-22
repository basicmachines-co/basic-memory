"""Doctor command for local consistency checks."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from loguru import logger
from mcp.server.fastmcp.exceptions import ToolError
from rich.console import Console
import typer

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.cli.commands.routing import force_routing, validate_routing_flags
from basic_memory.markdown.entity_parser import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.markdown.schemas import EntityFrontmatter, EntityMarkdown
from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.clients import KnowledgeClient, ProjectClient, SearchClient
from basic_memory.schemas.base import Entity
from basic_memory.schemas.project_info import ProjectInfoRequest
from basic_memory.schemas.search import SearchQuery
from basic_memory.schemas import SyncReportResponse

console = Console()


async def run_doctor() -> None:
    """Run local consistency checks for file <-> database flows."""
    console.print("[blue]Running Basic Memory doctor checks...[/blue]")

    project_name = f"doctor-{uuid.uuid4().hex[:8]}"
    api_note_title = "Doctor API Note"
    manual_note_title = "Doctor Manual Note"
    manual_permalink = "doctor/manual-note"

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        async with get_client() as client:
            project_client = ProjectClient(client)
            project_request = ProjectInfoRequest(
                name=project_name,
                path=str(temp_path),
                set_default=False,
            )

            project_id: str | None = None

            try:
                status = await project_client.create_project(project_request.model_dump())
                if not status.new_project:
                    raise ValueError("Failed to create doctor project")
                project_id = status.new_project.external_id
                console.print(f"[green]OK[/green] Created doctor project: {project_name}")

                # --- DB -> File: create an entity via API ---
                knowledge_client = KnowledgeClient(client, project_id)
                api_note = Entity(
                    title=api_note_title,
                    directory="doctor",
                    note_type="note",
                    content_type="text/markdown",
                    content=f"# {api_note_title}\n\n- [note] API to file check",
                    entity_metadata={"tags": ["doctor"]},
                )
                api_result = await knowledge_client.create_entity(api_note.model_dump(), fast=False)

                api_file = temp_path / api_result.file_path
                if not api_file.exists():
                    raise ValueError(f"API note file missing: {api_result.file_path}")

                api_text = api_file.read_text(encoding="utf-8")
                if api_note_title not in api_text:
                    raise ValueError("API note content missing from file")

                console.print("[green]OK[/green] API write created file")

                # --- File -> DB: write markdown file directly, then sync ---
                parser = EntityParser(temp_path)
                processor = MarkdownProcessor(parser)
                manual_markdown = EntityMarkdown(
                    frontmatter=EntityFrontmatter(
                        metadata={
                            "title": manual_note_title,
                            "type": "note",
                            "permalink": manual_permalink,
                            "tags": ["doctor"],
                        }
                    ),
                    content=f"# {manual_note_title}\n\n- [note] File to DB check",
                )

                manual_path = temp_path / "doctor" / "manual-note.md"
                await processor.write_file(manual_path, manual_markdown)
                console.print("[green]OK[/green] Manual file written")

                sync_data = await project_client.sync(
                    project_id, force_full=True, run_in_background=False
                )
                sync_report = SyncReportResponse.model_validate(sync_data)
                if sync_report.total == 0:
                    raise ValueError("Sync did not detect any changes")

                console.print("[green]OK[/green] Sync indexed manual file")

                search_client = SearchClient(client, project_id)
                search_query = SearchQuery(title=manual_note_title)
                search_results = await search_client.search(
                    search_query.model_dump(), page=1, page_size=5
                )
                if not any(result.title == manual_note_title for result in search_results.results):
                    raise ValueError("Manual note not found in search index")

                console.print("[green]OK[/green] Search confirmed manual file")

                status_report = await project_client.get_status(project_id)
                if status_report.total != 0:
                    raise ValueError("Project status not clean after sync")

                console.print("[green]OK[/green] Status clean after sync")

            finally:
                if project_id:
                    await project_client.delete_project(project_id)

    console.print("[green]Doctor checks passed.[/green]")


@app.command()
def doctor(
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
) -> None:
    """Run local consistency checks to verify file/database sync."""
    try:
        validate_routing_flags(local, cloud)
        with force_routing(local=local, cloud=cloud):
            run_with_cleanup(run_doctor())
    except (ToolError, ValueError) as e:
        console.print(f"[red]Doctor failed: {e}[/red]")
        raise typer.Exit(code=1)
    except Exception as e:
        logger.error(f"Doctor failed: {e}")
        typer.echo(f"Doctor failed: {e}", err=True)
        raise typer.Exit(code=1)  # pragma: no cover
