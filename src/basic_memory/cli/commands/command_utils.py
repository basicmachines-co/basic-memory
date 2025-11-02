"""utility functions for commands"""

from typing import Optional

from mcp.server.fastmcp.exceptions import ToolError
import typer

from rich.console import Console

from basic_memory.mcp.async_client import get_client

from basic_memory.mcp.tools.utils import call_post, call_get
from basic_memory.mcp.project_context import get_active_project
from basic_memory.schemas import ProjectInfoResponse

console = Console()


async def run_sync(project: Optional[str] = None, force_full: bool = False):
    """Run sync operation via API endpoint.

    Args:
        project: Optional project name
        force_full: If True, force a full scan bypassing watermark optimization
    """

    try:
        async with get_client() as client:
            project_item = await get_active_project(client, project, None)
            url = f"{project_item.project_url}/project/sync"
            if force_full:
                url += "?force_full=true"
            response = await call_post(client, url)
            data = response.json()
            console.print(f"[green]{data['message']}[/green]")
    except (ToolError, ValueError) as e:
        console.print(f"[red]Sync failed: {e}[/red]")
        raise typer.Exit(1)


async def get_project_info(project: str):
    """Get project information via API endpoint."""

    try:
        async with get_client() as client:
            project_item = await get_active_project(client, project, None)
            response = await call_get(client, f"{project_item.project_url}/project/info")
            return ProjectInfoResponse.model_validate(response.json())
    except (ToolError, ValueError) as e:
        console.print(f"[red]Sync failed: {e}[/red]")
        raise typer.Exit(1)
