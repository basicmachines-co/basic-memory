"""utility functions for commands"""

from typing import Optional

from mcp.server.fastmcp.exceptions import ToolError
import typer

from rich.console import Console

from basic_memory.cli.commands.cloud import get_authenticated_headers
from basic_memory.mcp.async_client import client

from basic_memory.mcp.tools.utils import call_post, call_get
from basic_memory.mcp.project_context import get_active_project
from basic_memory.schemas import ProjectInfoResponse

console = Console()


async def run_sync(project: Optional[str] = None):
    """Run sync operation via API endpoint."""

    try:
        from basic_memory.config import ConfigManager

        config = ConfigManager().config
        auth_headers = {}
        if config.cloud_mode_enabled:
            auth_headers = await get_authenticated_headers()

        project_item = await get_active_project(client, project, None, headers=auth_headers)
        response = await call_post(
            client, f"{project_item.project_url}/project/sync", headers=auth_headers
        )
        data = response.json()
        console.print(f"[green]✓ {data['message']}[/green]")
    except (ToolError, ValueError) as e:
        console.print(f"[red]✗ Sync failed: {e}[/red]")
        raise typer.Exit(1)


async def get_project_info(project: str):
    """Run sync operation via API endpoint."""

    try:
        from basic_memory.config import ConfigManager

        config = ConfigManager().config
        auth_headers = {}
        if config.cloud_mode_enabled:
            auth_headers = await get_authenticated_headers()

        project_item = await get_active_project(client, project, None, headers=auth_headers)
        response = await call_get(
            client, f"{project_item.project_url}/project/info", headers=auth_headers
        )
        return ProjectInfoResponse.model_validate(response.json())
    except (ToolError, ValueError) as e:
        console.print(f"[red]✗ Sync failed: {e}[/red]")
        raise typer.Exit(1)
