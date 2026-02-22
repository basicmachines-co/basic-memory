"""Workspace commands for Basic Memory cloud workspaces."""

import typer
from rich.console import Console
from rich.table import Table

from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.config import ConfigManager
from basic_memory.mcp.project_context import (
    _workspace_choices,
    _workspace_matches_identifier,
    get_available_workspaces,
)

console = Console()

workspace_app = typer.Typer(help="Manage cloud workspaces")


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

    config = ConfigManager().config
    default_ws = config.default_workspace

    table = Table(title="Available Workspaces")
    table.add_column("Name", style="cyan")
    table.add_column("Type", style="blue")
    table.add_column("Role", style="green")
    table.add_column("Tenant ID", style="yellow")
    table.add_column("Default", style="magenta")

    for workspace in workspaces:
        is_default = "[X]" if workspace.tenant_id == default_ws else ""
        table.add_row(
            workspace.name,
            workspace.workspace_type,
            workspace.role,
            workspace.tenant_id,
            is_default,
        )

    console.print(table)


@workspace_app.command("set-default")
def set_default_workspace(
    identifier: str = typer.Argument(..., help="Workspace name or tenant_id to set as default"),
) -> None:
    """Set the default cloud workspace.

    The default workspace is used as fallback when no per-project workspace
    is configured. Resolves the identifier against available workspaces.

    Examples:
      bm cloud workspace set-default Personal
      bm cloud workspace set-default 11111111-1111-1111-1111-111111111111
    """

    async def _list():
        return await get_available_workspaces()

    try:
        workspaces = run_with_cleanup(_list())
    except RuntimeError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)

    if not workspaces:
        console.print("[yellow]No accessible workspaces found.[/yellow]")
        raise typer.Exit(1)

    matches = [ws for ws in workspaces if _workspace_matches_identifier(ws, identifier)]

    if not matches:
        console.print(f"[red]Error: Workspace '{identifier}' not found[/red]")
        console.print(f"[dim]Available:\n{_workspace_choices(workspaces)}[/dim]")
        raise typer.Exit(1)

    if len(matches) > 1:
        console.print(
            f"[red]Error: Workspace name '{identifier}' matches multiple workspaces. "
            f"Use tenant_id instead.[/red]"
        )
        console.print(f"[dim]Available:\n{_workspace_choices(workspaces)}[/dim]")
        raise typer.Exit(1)

    selected = matches[0]
    config_manager = ConfigManager()
    config = config_manager.config
    config.default_workspace = selected.tenant_id
    config_manager.save_config(config)

    console.print(
        f"[green]Default workspace set to '{selected.name}' ({selected.tenant_id})[/green]"
    )
