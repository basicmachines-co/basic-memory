"""Status command for basic-memory CLI."""

import asyncio
import json
import time
from typing import Annotated, Optional

import typer
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.tree import Tree

from basic_memory.cli.app import app
from basic_memory.cli.commands.routing import force_routing, validate_routing_flags
from basic_memory.config import ConfigManager
from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.clients import ProjectClient
from basic_memory.schemas import ProjectIndexStatusResponse
from basic_memory.schemas.project_readiness import ProjectIndexPhase
from basic_memory.mcp.project_context import get_active_project

# Create rich console
console = Console()


def add_observed_files_to_tree(tree: Tree, status: ProjectIndexStatusResponse) -> None:
    """Add observed project-index files to the tree, grouped by directory."""
    by_dir: dict[str, list[tuple[str, str, str | None]]] = {}
    for observed_file in status.observed_files:
        path = observed_file.path
        parts = path.split("/", 1)
        dir_name = parts[0] if len(parts) > 1 else ""
        file_name = parts[1] if len(parts) > 1 else parts[0]
        checksum = observed_file.checksum[:8] if observed_file.checksum else None
        by_dir.setdefault(dir_name, []).append((file_name, path, checksum))

    for dir_name, files in sorted(by_dir.items()):
        if dir_name:
            branch = tree.add(f"[bold]{dir_name}/[/bold]")
        else:
            branch = tree

        for file_name, _, checksum in sorted(files):
            if checksum:
                branch.add(f"[cyan]{file_name}[/cyan] ({checksum})")
            else:
                branch.add(f"[cyan]{file_name}[/cyan]")


def display_project_index_status(
    project_name: str,
    title: str,
    status: ProjectIndexStatusResponse,
    verbose: bool = False,
) -> None:
    """Display project-index observation status using Rich."""
    readiness = status.readiness
    tree = Tree(f"{project_name}: {title}")
    tree.add(f"{status.total_files} observed file{'s' if status.total_files != 1 else ''}")

    # A file count alone is what let a never-indexed project look finished
    # (#1414). Lead with the phase, then the per-stage numbers a caller waits on.
    phase_color = "red" if readiness.phase is ProjectIndexPhase.NEVER_INDEXED else "green"
    tree.add(f"[{phase_color}]{readiness.describe(project_name)}[/{phase_color}]")

    stages_branch = tree.add("[cyan]Stages[/cyan]")
    for stage in readiness.stages:
        stages_branch.add(
            f"{stage.name}: [bold]{stage.phase}[/bold] "
            f"({stage.completed}/{stage.total} done, {stage.pending} pending)"
        )

    if verbose and status.observed_files:
        files_branch = tree.add("[cyan]Observed Files[/cyan]")
        add_observed_files_to_tree(files_branch, status)

    console.print(Panel(tree, expand=False))


async def run_status(
    project: Optional[str] = None,
    wait: bool = False,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
) -> tuple[str, ProjectIndexStatusResponse]:
    """Fetch current project-index observation and readiness.

    ``wait`` polls until every readiness stage settles, or until ``timeout``
    elapses. It used to be a documented no-op because the only signal available
    was a pending count, which reads zero both when work has drained and when
    none was ever queued (#1414); the readiness phase now separates those, so
    waiting can mean something. A never-indexed project will keep the loop
    running until the timeout, which is the honest answer -- nothing is coming
    unless something starts an index pass.

    Returns (project_name, project_index_status) for the caller to render.

    """
    # Resolve default project so get_client() can route per-project
    project = project or ConfigManager().default_project

    # Reuse a single client/context across polls so we don't reconnect each loop.
    async with get_client(project_name=project) as client:
        project_item = await get_active_project(client, project, None)
        project_client = ProjectClient(client)

        # Trigger: caller did not request --wait
        # Why: preserve the original single-scan behavior for the common case
        # Outcome: one status scan, returned as-is
        if not wait:
            project_index_status = await project_client.get_status(project_item.external_id)
            return project_item.name, project_index_status

        deadline = time.monotonic() + timeout
        while True:
            project_index_status = await project_client.get_status(project_item.external_id)
            if project_index_status.readiness.phase is ProjectIndexPhase.IDLE:
                return project_item.name, project_index_status
            if time.monotonic() >= deadline:
                logger.debug(
                    "status --wait timed out before the project index settled",
                    phase=project_index_status.readiness.phase,
                    timeout=timeout,
                )
                return project_item.name, project_index_status
            await asyncio.sleep(poll_interval)


@app.command()
def status(
    project: Annotated[
        Optional[str],
        typer.Option(help="The project name."),
    ] = None,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed file information"),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
    wait: bool = typer.Option(
        False,
        "--wait",
        help="Poll until every readiness stage settles (or --timeout elapses)",
    ),
    timeout: float = typer.Option(30.0, "--timeout", help="Seconds to wait when --wait is set"),
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Show current project-index observation status and readiness.

    Use --json for machine-readable output; `readiness.phase` distinguishes a
    project that was never indexed from one that is indexed and idle, and
    `readiness.stages` reports file indexing, relation resolution, and
    embeddings separately.
    Use --wait to block until every stage settles.
    Use --local to force local routing when cloud mode is enabled.
    Use --cloud to force cloud routing when cloud mode is disabled.
    """
    from basic_memory.cli.commands.command_utils import run_with_cleanup

    # Deferred: ToolError lives in FastMCP's runtime, which must not load at CLI startup (#886).
    from fastmcp.exceptions import ToolError

    # Trigger: --wait with a negative --timeout
    # Why: a negative deadline times out on the very first poll, producing a confusing
    #      "Timed out after -5s" message instead of flagging the bad input. Raised
    #      before the try/except so typer renders a clean usage error (exit 2).
    # Outcome: reject it up front with a clear parameter error.
    if wait and timeout < 0:
        raise typer.BadParameter("--timeout must be >= 0", param_hint="'--timeout'")

    try:
        validate_routing_flags(local, cloud)
        # Trigger: no explicit routing flag provided
        # Why: status scans the local filesystem — cloud routing would use the
        #      Docker-internal path stored in the cloud database, which doesn't
        #      exist locally.
        # Outcome: default to local routing unless --cloud was explicitly requested.
        if not local and not cloud:
            local = True
        with force_routing(local=local, cloud=cloud):
            project_name, project_index_status = run_with_cleanup(
                run_status(project, wait=wait, timeout=timeout)
            )

        if json_output:
            print(
                json.dumps(
                    project_index_status.model_dump(mode="json"),
                    indent=2,
                    default=str,
                )
            )
        else:
            display_project_index_status(
                project_name,
                "Project Index",
                project_index_status,
                verbose,
            )
    except (ValueError, ToolError) as e:
        if json_output:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        logger.error(f"Error checking status: {e}")
        if json_output:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            typer.echo(f"Error checking status: {e}", err=True)
        raise typer.Exit(code=1)  # pragma: no cover
