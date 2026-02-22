"""utility functions for commands"""

import asyncio
from typing import Optional, TypeVar, Coroutine, Any

from mcp.server.fastmcp.exceptions import ToolError
import typer

from rich.console import Console

from basic_memory import db
from basic_memory.config import ConfigManager
from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.clients import ProjectClient
from basic_memory.mcp.project_context import get_active_project

console = Console()

T = TypeVar("T")


def run_with_cleanup(coro: Coroutine[Any, Any, T]) -> T:
    """Run an async coroutine with proper database cleanup.

    This helper ensures database connections are cleaned up before the
    event loop closes, preventing process hangs in CLI commands.

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


async def run_sync(
    project: Optional[str] = None,
    force_full: bool = False,
    run_in_background: bool = True,
):
    """Run sync operation via API endpoint.

    Args:
        project: Optional project name
        force_full: If True, force a full scan bypassing watermark optimization
        run_in_background: If True, return immediately; if False, wait for completion
    """

    # Resolve default project so get_client() can route per-project
    project = project or ConfigManager().default_project

    try:
        async with get_client(project_name=project) as client:
            project_item = await get_active_project(client, project, None)
            project_client = ProjectClient(client)
            data = await project_client.sync(
                project_item.external_id,
                force_full=force_full,
                run_in_background=run_in_background,
            )
            # Background mode returns {"message": "..."}, foreground returns SyncReportResponse
            if "message" in data:
                console.print(f"[green]{data['message']}[/green]")
            else:
                # Foreground mode - show summary of sync results
                total = data.get("total", 0)
                new_count = len(data.get("new", []))
                modified_count = len(data.get("modified", []))
                deleted_count = len(data.get("deleted", []))
                console.print(
                    f"[green]Synced {total} files[/green] "
                    f"(new: {new_count}, modified: {modified_count}, deleted: {deleted_count})"
                )
    except (ToolError, ValueError) as e:
        console.print(f"[red]Sync failed: {e}[/red]")
        raise typer.Exit(1)


async def get_project_info(project: str):
    """Get project information via API endpoint."""
    try:
        async with get_client(project_name=project) as client:
            project_item = await get_active_project(client, project, None)
            return await ProjectClient(client).get_info(project_item.external_id)
    except (ToolError, ValueError) as e:
        error_text = str(e)
        if "internal proxy error" in error_text.lower() and "not found in configuration" in (
            error_text.lower()
        ):
            console.print(
                "[red]Project info failed: cloud returned an internal configuration error for "
                "this project.[/red]"
            )
            console.print(
                "[yellow]This is a cloud backend issue for detailed info lookups. "
                "Use `bm project list --cloud` for project metadata until the service is updated."
                "[/yellow]"
            )
        else:
            console.print(f"[red]Project info failed: {e}[/red]")
        raise typer.Exit(1)
