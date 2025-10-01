"""utility functions for commands"""

from typing import Optional

from mcp.server.fastmcp.exceptions import ToolError
import typer
from rich.console import Console
from basic_memory.mcp.async_client import client

from basic_memory.mcp.tools.utils import call_post
from basic_memory.mcp.project_context import get_active_project

console = Console()


async def run_sync(project: Optional[str] = None):
    """Run sync operation via API endpoint."""

    try:
        project_item = await get_active_project(client, project, None)
        response = await call_post(client, f"{project_item.project_url}/project/sync")
        data = response.json()
        console.print(f"[green]✓ {data['message']}[/green]")
    except (ToolError, ValueError) as e:
        console.print(f"[red]✗ Sync failed: {e}[/red]")
        raise typer.Exit(1)
