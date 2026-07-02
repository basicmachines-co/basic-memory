"""Status command for basic-memory CLI."""

import json
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
    tree = Tree(f"{project_name}: {title}")
    tree.add(f"{status.total_files} observed file{'s' if status.total_files != 1 else ''}")

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
    """Fetch current project-index observation status.

    The event-index flow no longer exposes a pending-change counter. The watcher
    is the incremental path, and explicit project indexing is a full fanout.
    ``wait`` is accepted as a compatibility flag and returns the current
    observation immediately.

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

        logger.debug(
            "status --wait is a compatibility no-op for event-based project indexing",
            timeout=timeout,
            poll_interval=poll_interval,
        )
        project_index_status = await project_client.get_status(project_item.external_id)
        return project_item.name, project_index_status


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
        help="Compatibility flag; returns the current project-index observation",
    ),
    timeout: float = typer.Option(30.0, "--timeout", help="Compatibility option for --wait"),
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Show current project-index observation status.

    Use --json for machine-readable output.
    The --wait flag is accepted for compatibility and returns the current
    project-index observation immediately.
    Use --local to force local routing when cloud mode is enabled.
    Use --cloud to force cloud routing when cloud mode is disabled.
    """
    from basic_memory.cli.commands.command_utils import run_with_cleanup

    # Deferred: ToolError lives in the mcp SDK, which must not load at CLI startup (#886).
    from mcp.server.fastmcp.exceptions import ToolError

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
