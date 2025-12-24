"""utility functions for commands"""

import asyncio
from typing import Optional, TypeVar, Coroutine, Any

from mcp.server.fastmcp.exceptions import ToolError
import typer

from rich.console import Console

from basic_memory import db
from basic_memory.mcp.async_client import get_client

from basic_memory.mcp.tools.utils import call_post, call_get
from basic_memory.mcp.project_context import get_active_project
from basic_memory.schemas import ProjectInfoResponse

console = Console()

T = TypeVar("T")


def run_with_cleanup(coro: Coroutine[Any, Any, T]) -> T:
    """Run an async coroutine with proper database cleanup.

    This helper ensures database connections are cleaned up before the event
    loop closes, preventing process hangs in CLI commands.

    Args:
        coro: The coroutine to run

    Returns:
        The result of the coroutine
    """

    async def _with_cleanup() -> T:
        try:
            return await coro
        finally:
            await db.shutdown_db()

    return asyncio.run(_with_cleanup())


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
