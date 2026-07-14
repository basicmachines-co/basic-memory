"""utility functions for commands"""

import asyncio
from typing import Optional, TypeVar, Coroutine, Any

import typer

from rich.console import Console

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
    # Deferred: basic_memory.db pulls SQLAlchemy + Alembic, which must not load
    # at CLI import time — only when a command actually runs (#886).
    from basic_memory import db
    from basic_memory.cloud.note_content_materialization import drain_pending_materializations
    from basic_memory.index.local_schedulers import drain_background_tasks

    async def _with_cleanup() -> T:
        try:
            return await coro
        finally:
            # Drain queued source-of-truth file writes before the loop closes:
            # local writes accept and materialize off the request path, so a
            # one-shot client would otherwise exit before the markdown file is
            # written (the API already reported the note accepted).
            await drain_pending_materializations()
            # Then the follow-up work those writes scheduled (vector sync,
            # relation resolution): cancelling it at loop close would leave
            # semantic search and inbound wikilinks stale until a later reindex.
            await drain_background_tasks()
            await db.shutdown_db()

    return asyncio.run(_with_cleanup())


async def run_project_index(
    project: Optional[str] = None,
    force_full: bool = False,
    run_in_background: bool = True,
):
    """Run project indexing via API endpoint.

    Args:
        project: Optional project name
        force_full: If True, force a full scan bypassing watermark optimization
        run_in_background: If True, return immediately; if False, wait for completion
    """
    # Deferred: ToolError lives in the mcp SDK, which must not load at CLI startup (#886).
    from mcp.server.fastmcp.exceptions import ToolError

    # Resolve default project so get_client() can route per-project
    project = project or ConfigManager().default_project

    try:
        async with get_client(project_name=project) as client:
            project_item = await get_active_project(client, project, None)
            project_client = ProjectClient(client)
            data = await project_client.index(
                project_item.external_id,
                force_full=force_full,
                run_in_background=run_in_background,
            )
            # Background mode returns {"message": "..."}, foreground returns project-index counts.
            if "message" in data:
                console.print(f"[green]{data['message']}[/green]")
            else:
                total_files = data.get("total_files", 0)
                enqueued_files = data.get("enqueued_files", 0)
                enqueued_batches = data.get("enqueued_batches", 0)
                deleted_files = data.get("deleted_files", 0)
                console.print(
                    f"[green]Indexed {enqueued_files}/{total_files} files[/green] "
                    f"(batches: {enqueued_batches}, deleted orphans: {deleted_files})"
                )
    except (ToolError, ValueError) as e:
        console.print(f"[red]Index failed: {e}[/red]")
        raise typer.Exit(1)


async def get_project_info(project: str):
    """Get project information via API endpoint."""
    # Deferred: ToolError lives in the mcp SDK, which must not load at CLI startup (#886).
    from mcp.server.fastmcp.exceptions import ToolError

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
