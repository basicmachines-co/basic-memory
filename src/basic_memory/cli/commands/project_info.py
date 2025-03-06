"""CLI command for project info status."""

import asyncio
import json
from datetime import datetime

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.tree import Tree

from basic_memory.cli.app import app
from basic_memory.mcp.tools.project_info import project_info


info_app = typer.Typer()
app.add_typer(info_app, name="info", help="Get information about your Basic Memory project")


@info_app.command("stats")
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
