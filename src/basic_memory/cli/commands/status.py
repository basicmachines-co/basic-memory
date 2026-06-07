"""Status command for basic-memory CLI."""

import asyncio
import json
import time
from typing import Set, Dict
from typing import Annotated, Optional

from mcp.server.fastmcp.exceptions import ToolError
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
from basic_memory.schemas import SyncReportResponse
from basic_memory.mcp.project_context import get_active_project

# Create rich console
console = Console()


def add_files_to_tree(
    tree: Tree, paths: Set[str], style: str, checksums: Dict[str, str] | None = None
):
    """Add files to tree, grouped by directory."""
    # Group by directory
    by_dir = {}
    for path in sorted(paths):
        parts = path.split("/", 1)
        dir_name = parts[0] if len(parts) > 1 else ""
        file_name = parts[1] if len(parts) > 1 else parts[0]
        by_dir.setdefault(dir_name, []).append((file_name, path))

    # Add to tree
    for dir_name, files in sorted(by_dir.items()):
        if dir_name:
            branch = tree.add(f"[bold]{dir_name}/[/bold]")
        else:
            branch = tree

        for file_name, full_path in sorted(files):
            if checksums and full_path in checksums:
                checksum_short = checksums[full_path][:8]
                branch.add(f"[{style}]{file_name}[/{style}] ({checksum_short})")
            else:
                branch.add(f"[{style}]{file_name}[/{style}]")


def group_changes_by_directory(changes: SyncReportResponse) -> Dict[str, Dict[str, int]]:
    """Group changes by directory for summary view."""
    by_dir = {}
    for change_type, paths in [
        ("new", changes.new),
        ("modified", changes.modified),
        ("deleted", changes.deleted),
    ]:
        for path in paths:
            dir_name = path.split("/", 1)[0]
            by_dir.setdefault(dir_name, {"new": 0, "modified": 0, "deleted": 0, "moved": 0})
            by_dir[dir_name][change_type] += 1

    # Handle moves - count in both source and destination directories
    for old_path, new_path in changes.moves.items():
        old_dir = old_path.split("/", 1)[0]
        new_dir = new_path.split("/", 1)[0]
        by_dir.setdefault(old_dir, {"new": 0, "modified": 0, "deleted": 0, "moved": 0})
        by_dir.setdefault(new_dir, {"new": 0, "modified": 0, "deleted": 0, "moved": 0})
        by_dir[old_dir]["moved"] += 1
        if old_dir != new_dir:
            by_dir[new_dir]["moved"] += 1

    return by_dir


def build_directory_summary(counts: Dict[str, int]) -> str:
    """Build summary string for directory changes."""
    parts = []
    if counts["new"]:
        parts.append(f"[green]+{counts['new']} new[/green]")
    if counts["modified"]:
        parts.append(f"[yellow]~{counts['modified']} modified[/yellow]")
    if counts["moved"]:
        parts.append(f"[blue]↔{counts['moved']} moved[/blue]")
    if counts["deleted"]:
        parts.append(f"[red]-{counts['deleted']} deleted[/red]")
    return " ".join(parts)


def display_changes(
    project_name: str, title: str, changes: SyncReportResponse, verbose: bool = False
):
    """Display changes using Rich for better visualization."""
    tree = Tree(f"{project_name}: {title}")

    if changes.total == 0 and not changes.skipped_files:
        tree.add("No changes")
        console.print(Panel(tree, expand=False))
        return

    if verbose:
        # Full file listing with checksums
        if changes.new:
            new_branch = tree.add("[green]New Files[/green]")
            add_files_to_tree(new_branch, changes.new, "green", changes.checksums)
        if changes.modified:
            mod_branch = tree.add("[yellow]Modified[/yellow]")
            add_files_to_tree(mod_branch, changes.modified, "yellow", changes.checksums)
        if changes.moves:
            move_branch = tree.add("[blue]Moved[/blue]")
            for old_path, new_path in sorted(changes.moves.items()):
                move_branch.add(f"[blue]{old_path}[/blue] → [blue]{new_path}[/blue]")
        if changes.deleted:
            del_branch = tree.add("[red]Deleted[/red]")
            add_files_to_tree(del_branch, changes.deleted, "red")
        if changes.skipped_files:
            skip_branch = tree.add("[red]! Skipped (Circuit Breaker)[/red]")
            for skipped in sorted(changes.skipped_files, key=lambda x: x.path):
                skip_branch.add(
                    f"[red]{skipped.path}[/red] "
                    f"(failures: {skipped.failure_count}, reason: {skipped.reason})"
                )
    else:
        # Show directory summaries
        by_dir = group_changes_by_directory(changes)
        for dir_name, counts in sorted(by_dir.items()):
            summary = build_directory_summary(counts)
            if summary:  # Only show directories with changes
                tree.add(f"[bold]{dir_name}/[/bold] {summary}")

        # Show skipped files summary in non-verbose mode
        if changes.skipped_files:
            skip_count = len(changes.skipped_files)
            tree.add(
                f"[red]! {skip_count} file{'s' if skip_count != 1 else ''} "
                f"skipped due to repeated failures[/red]"
            )

    console.print(Panel(tree, expand=False))


class StatusTimeout(Exception):
    """Raised when --wait does not reach a synced state before the deadline."""


async def run_status(
    project: Optional[str] = None,
    wait: bool = False,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
) -> tuple[str, SyncReportResponse]:
    """Fetch sync status of files vs database.

    When ``wait`` is False this performs a single live disk-vs-DB scan and
    returns immediately. When ``wait`` is True it polls until the project has
    no pending changes (``sync_report.total == 0``) or the timeout elapses.

    Returns (project_name, sync_report) for the caller to render.

    Raises:
        StatusTimeout: If ``wait`` is True and the deadline passes before the
            project reaches a synced state.
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
            sync_report = await project_client.get_status(project_item.external_id)
            return project_item.name, sync_report

        # Trigger: --wait requested
        # Why: callers (bulk imports, benchmarks, tests) need to block until the
        #      index has caught up instead of polling externally
        # Outcome: poll get_status until total == 0 or the deadline is reached
        deadline = time.monotonic() + timeout
        while True:
            sync_report = await project_client.get_status(project_item.external_id)
            if sync_report.total == 0:
                return project_item.name, sync_report
            if time.monotonic() >= deadline:
                raise StatusTimeout(
                    f"Timed out after {timeout:g}s waiting for '{project_item.name}' "
                    f"to finish indexing ({sync_report.total} pending change(s) remaining)."
                )
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
        False, "--wait", help="Block until indexing is complete (no pending changes)"
    ),
    timeout: float = typer.Option(30.0, "--timeout", help="Max seconds to wait when --wait is set"),
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Show sync status between files and database.

    Use --json for machine-readable output.
    Use --wait to block until indexing is complete (e.g. after a bulk import);
    combine with --timeout to bound the wait. On timeout the command exits 1.
    Use --local to force local routing when cloud mode is enabled.
    Use --cloud to force cloud routing when cloud mode is disabled.
    """
    from basic_memory.cli.commands.command_utils import run_with_cleanup

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
            project_name, sync_report = run_with_cleanup(
                run_status(project, wait=wait, timeout=timeout)
            )

        if json_output:
            print(json.dumps(sync_report.model_dump(mode="json"), indent=2, default=str))
        else:
            display_changes(project_name, "Status", sync_report, verbose)
    except StatusTimeout as e:
        # Trigger: --wait deadline passed before the project finished indexing
        # Why: callers depend on exit code 1 to detect that indexing did not
        #      complete in time, while still getting a clear machine/human message
        # Outcome: emit the timeout message (JSON-shaped under --json) and exit 1
        if json_output:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
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
