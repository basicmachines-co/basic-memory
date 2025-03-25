"""Command module for basic-memory project management."""

import asyncio
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from basic_memory.cli.app import app
from basic_memory.config import ConfigManager, config
from basic_memory.mcp.tools.project_info import project_info
import json
from datetime import datetime

from rich.panel import Panel
from rich.tree import Tree

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
    """Set the default project and activate it for the current session."""
    config_manager = ConfigManager()

    try:
        # Set the default project
        config_manager.set_default_project(name)

        # Also activate it for the current session by setting the environment variable
        os.environ["BASIC_MEMORY_PROJECT"] = name

        # Reload configuration to apply the change
        from importlib import reload
        from basic_memory import config as config_module

        reload(config_module)
        console.print(f"[green]Project '{name}' set as default and activated[/green]")
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


@project_app.command("info")
def display_project_info(
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
):
    """Display detailed information and statistics about the current project."""
    try:
        # Get project info
        info = asyncio.run(project_info())

        if json_output:
            # Convert to JSON and print
            print(json.dumps(info.model_dump(), indent=2, default=str))
        else:
            # Create rich display
            console = Console()

            # Project configuration section
            console.print(
                Panel(
                    f"[bold]Project:[/bold] {info.project_name}\n"
                    f"[bold]Path:[/bold] {info.project_path}\n"
                    f"[bold]Default Project:[/bold] {info.default_project}\n",
                    title="📊 Basic Memory Project Info",
                    expand=False,
                )
            )

            # Statistics section
            stats_table = Table(title="📈 Statistics")
            stats_table.add_column("Metric", style="cyan")
            stats_table.add_column("Count", style="green")

            stats_table.add_row("Entities", str(info.statistics.total_entities))
            stats_table.add_row("Observations", str(info.statistics.total_observations))
            stats_table.add_row("Relations", str(info.statistics.total_relations))
            stats_table.add_row(
                "Unresolved Relations", str(info.statistics.total_unresolved_relations)
            )
            stats_table.add_row("Isolated Entities", str(info.statistics.isolated_entities))

            console.print(stats_table)

            # Entity types
            if info.statistics.entity_types:
                entity_types_table = Table(title="📑 Entity Types")
                entity_types_table.add_column("Type", style="blue")
                entity_types_table.add_column("Count", style="green")

                for entity_type, count in info.statistics.entity_types.items():
                    entity_types_table.add_row(entity_type, str(count))

                console.print(entity_types_table)

            # Most connected entities
            if info.statistics.most_connected_entities:
                connected_table = Table(title="🔗 Most Connected Entities")
                connected_table.add_column("Title", style="blue")
                connected_table.add_column("Permalink", style="cyan")
                connected_table.add_column("Relations", style="green")

                for entity in info.statistics.most_connected_entities:
                    connected_table.add_row(
                        entity["title"], entity["permalink"], str(entity["relation_count"])
                    )

                console.print(connected_table)

            # Recent activity
            if info.activity.recently_updated:
                recent_table = Table(title="🕒 Recent Activity")
                recent_table.add_column("Title", style="blue")
                recent_table.add_column("Type", style="cyan")
                recent_table.add_column("Last Updated", style="green")

                for entity in info.activity.recently_updated[:5]:  # Show top 5
                    updated_at = (
                        datetime.fromisoformat(entity["updated_at"])
                        if isinstance(entity["updated_at"], str)
                        else entity["updated_at"]
                    )
                    recent_table.add_row(
                        entity["title"],
                        entity["entity_type"],
                        updated_at.strftime("%Y-%m-%d %H:%M"),
                    )

                console.print(recent_table)

            # System status
            system_tree = Tree("🖥️ System Status")
            system_tree.add(f"Basic Memory version: [bold green]{info.system.version}[/bold green]")
            system_tree.add(
                f"Database: [cyan]{info.system.database_path}[/cyan] ([green]{info.system.database_size}[/green])"
            )

            # Watch status
            if info.system.watch_status:  # pragma: no cover
                watch_branch = system_tree.add("Watch Service")
                running = info.system.watch_status.get("running", False)
                status_color = "green" if running else "red"
                watch_branch.add(
                    f"Status: [bold {status_color}]{'Running' if running else 'Stopped'}[/bold {status_color}]"
                )

                if running:
                    start_time = (
                        datetime.fromisoformat(info.system.watch_status.get("start_time", ""))
                        if isinstance(info.system.watch_status.get("start_time"), str)
                        else info.system.watch_status.get("start_time")
                    )
                    watch_branch.add(
                        f"Running since: [cyan]{start_time.strftime('%Y-%m-%d %H:%M')}[/cyan]"
                    )
                    watch_branch.add(
                        f"Files synced: [green]{info.system.watch_status.get('synced_files', 0)}[/green]"
                    )
                    watch_branch.add(
                        f"Errors: [{'red' if info.system.watch_status.get('error_count', 0) > 0 else 'green'}]{info.system.watch_status.get('error_count', 0)}[/{'red' if info.system.watch_status.get('error_count', 0) > 0 else 'green'}]"
                    )
            else:
                system_tree.add("[yellow]Watch service not running[/yellow]")

            console.print(system_tree)

            # Available projects
            projects_table = Table(title="📁 Available Projects")
            projects_table.add_column("Name", style="blue")
            projects_table.add_column("Path", style="cyan")
            projects_table.add_column("Default", style="green")

            for name, path in info.available_projects.items():
                is_default = name == info.default_project
                projects_table.add_row(name, path, "✓" if is_default else "")

            console.print(projects_table)

            # Timestamp
            current_time = (
                datetime.fromisoformat(str(info.system.timestamp))
                if isinstance(info.system.timestamp, str)
                else info.system.timestamp
            )
            console.print(f"\nTimestamp: [cyan]{current_time.strftime('%Y-%m-%d %H:%M:%S')}[/cyan]")

    except Exception as e:  # pragma: no cover
        typer.echo(f"Error getting project info: {e}", err=True)
        raise typer.Exit(1)
