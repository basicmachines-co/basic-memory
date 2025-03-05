"""Command module for basic-memory project management."""

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from basic_memory.cli.app import app
from basic_memory.config import ConfigManager, config

console = Console()

# Create a project subcommand
project_app = typer.Typer(help="Manage multiple Basic Memory projects")
app.add_typer(project_app, name="project")


def format_path(path: str) -> str:
    """Format a path for display, using ~ for home directory."""
    home = str(Path.home())
    if path.startswith(home):
        return path.replace(home, "~", 1)
    return path


@project_app.command("list")
def list_projects() -> None:
    """List all configured projects."""
    config_manager = ConfigManager()
    projects = config_manager.projects

    table = Table(title="Basic Memory Projects")
    table.add_column("Name", style="cyan")
    table.add_column("Path", style="green")
    table.add_column("Default", style="yellow")
    table.add_column("Active", style="magenta")

    default_project = config_manager.default_project
    active_project = config.project

    for name, path in projects.items():
        is_default = "✓" if name == default_project else ""
        is_active = "✓" if name == active_project else ""
        table.add_row(name, format_path(path), is_default, is_active)

    console.print(table)


@project_app.command("add")
def add_project(
    name: str = typer.Argument(..., help="Name of the project"),
    path: str = typer.Argument(..., help="Path to the project directory"),
) -> None:
    """Add a new project."""
    config_manager = ConfigManager()

    try:
        # Resolve to absolute path
        resolved_path = os.path.abspath(os.path.expanduser(path))
        config_manager.add_project(name, resolved_path)
        console.print(f"[green]Project '{name}' added at {format_path(resolved_path)}[/green]")

        # Display usage hint
        console.print("\nTo use this project:")
        console.print(f"  basic-memory --project={name} <command>")
        console.print("  # or")
        console.print(f"  basic-memory project default {name}")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@project_app.command("remove")
def remove_project(
    name: str = typer.Argument(..., help="Name of the project to remove"),
) -> None:
    """Remove a project from configuration."""
    config_manager = ConfigManager()

    try:
        config_manager.remove_project(name)
        console.print(f"[green]Project '{name}' removed from configuration[/green]")
        console.print("[yellow]Note: The project files have not been deleted from disk.[/yellow]")
    except ValueError as e:  # pragma: no cover
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@project_app.command("default")
def set_default_project(
    name: str = typer.Argument(..., help="Name of the project to set as default"),
) -> None:
    """Set the default project."""
    config_manager = ConfigManager()

    try:
        config_manager.set_default_project(name)
        console.print(f"[green]Project '{name}' set as default[/green]")
    except ValueError as e:  # pragma: no cover
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@project_app.command("current")
def show_current_project() -> None:
    """Show the current project."""
    config_manager = ConfigManager()
    current = os.environ.get("BASIC_MEMORY_PROJECT", config_manager.default_project)

    try:
        path = config_manager.get_project_path(current)
        console.print(f"Current project: [cyan]{current}[/cyan]")
        console.print(f"Path: [green]{format_path(str(path))}[/green]")
        console.print(f"Database: [blue]{format_path(str(config.database_path))}[/blue]")
    except ValueError:  # pragma: no cover
        console.print(f"[yellow]Warning: Project '{current}' not found in configuration[/yellow]")
        console.print(f"Using default project: [cyan]{config_manager.default_project}[/cyan]")
