"""Orphans command - show entities with no relations in the knowledge graph."""

import json
from typing import Annotated, Optional

import typer
from loguru import logger
from mcp.server.fastmcp.exceptions import ToolError
from rich.console import Console
from rich.table import Table

from basic_memory.cli.app import app
from basic_memory.cli.commands.routing import force_routing, validate_routing_flags
from basic_memory.config import ConfigManager
from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.clients.knowledge import KnowledgeClient
from basic_memory.mcp.project_context import get_active_project
from basic_memory.schemas.v2.graph import GraphNode

console = Console()


async def run_orphans(project: Optional[str] = None) -> tuple[str, list[GraphNode]]:
    """Fetch entities that have no relations in the knowledge graph."""
    project = project or ConfigManager().default_project

    async with get_client(project_name=project) as client:
        project_item = await get_active_project(client, project, None)
        entities = await KnowledgeClient(client, project_item.external_id).get_orphans()
        return project_item.name, entities


@app.command()
def orphans(
    project: Annotated[
        Optional[str],
        typer.Option(help="The project name."),
    ] = None,
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Show entities that have no relations in the knowledge graph.

    Orphan entities have no incoming or outgoing connections. These may indicate
    newly created notes not yet linked to other entities, or notes that have had
    their relations removed.
    """
    from basic_memory.cli.commands.command_utils import run_with_cleanup

    try:
        validate_routing_flags(local, cloud)
        with force_routing(local=local, cloud=cloud):
            project_name, entities = run_with_cleanup(run_orphans(project))

        if json_output:
            print(json.dumps([entity.model_dump(mode="json") for entity in entities], indent=2))
            return

        if not entities:
            console.print(f"[green]No orphan entities in project '{project_name}'[/green]")
            return

        table = Table(title=f"{project_name}: Entities Without Relations ({len(entities)} total)")
        table.add_column("Title", style="cyan")
        table.add_column("File Path", style="yellow")
        table.add_column("Type", style="green")

        for entity in entities:
            table.add_row(
                entity.title,
                entity.file_path,
                entity.note_type or "",
            )

        console.print(table)
    except (ValueError, ToolError) as exc:
        if json_output:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as exc:
        logger.error(f"Error fetching orphan entities: {exc}")
        if json_output:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=1)  # pragma: no cover
