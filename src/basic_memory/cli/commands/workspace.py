"""Workspace commands for Basic Memory cloud workspaces."""

import typer
from rich.console import Console
from rich.table import Table

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.mcp.project_context import get_available_workspaces

console = Console()

workspace_app = typer.Typer(help="Manage cloud workspaces")
app.add_typer(workspace_app, name="workspace")


@workspace_app.command("list")
def list_workspaces() -> None:
    """List cloud workspaces available to the current OAuth session."""

    async def _list():
        return await get_available_workspaces()

    try:
        workspaces = run_with_cleanup(_list())
    except RuntimeError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)
    except Exception as exc:  # pragma: no cover
        console.print(f"[red]Error listing workspaces: {exc}[/red]")
        raise typer.Exit(1)

    if not workspaces:
        console.print("[yellow]No accessible workspaces found.[/yellow]")
        return

    table = Table(title="Available Workspaces")
    table.add_column("Name", style="cyan")
    table.add_column("Type", style="blue")
    table.add_column("Role", style="green")
    table.add_column("Tenant ID", style="yellow")

    for workspace in workspaces:
        table.add_row(
            workspace.name,
            workspace.workspace_type,
            workspace.role,
            workspace.tenant_id,
        )

    console.print(table)


@app.command("workspaces")
def workspaces_alias() -> None:
    """Alias for `bm workspace list`."""
    list_workspaces()
