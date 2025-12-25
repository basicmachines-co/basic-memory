"""CLI commands for telemetry management."""

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from basic_memory.config import ConfigManager
from basic_memory.telemetry import get_install_id, is_telemetry_enabled

app = typer.Typer(help="Manage anonymous usage telemetry")
console = Console()


@app.command()
def enable():
    """Enable anonymous usage telemetry."""
    try:
        config_manager = ConfigManager()
        config = config_manager.load_config()
        config.telemetry_enabled = True
        config_manager.save_config(config)

        console.print("[green]✓[/green] Telemetry enabled")
        console.print(
            "Thank you for helping improve Basic Memory! Details: https://memory.basicmachines.co/telemetry"
        )
    except Exception as e:
        console.print(f"[red]Error enabling telemetry:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def disable():
    """Disable anonymous usage telemetry."""
    try:
        config_manager = ConfigManager()
        config = config_manager.load_config()
        config.telemetry_enabled = False
        config_manager.save_config(config)

        console.print("[green]✓[/green] Telemetry disabled")
        console.print(
            "You can delete your installation ID at: ~/.basic-memory/.install_id"
        )
    except Exception as e:
        console.print(f"[red]Error disabling telemetry:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def status():
    """Show current telemetry status and what data is collected."""
    try:
        enabled = is_telemetry_enabled()
        install_id = get_install_id()

        # Status table
        status_table = Table(show_header=False, box=None)
        status_table.add_row("Status", "[green]Enabled[/green]" if enabled else "[red]Disabled[/red]")
        status_table.add_row("Installation ID", install_id)
        status_table.add_row("ID File", "~/.basic-memory/.install_id")

        console.print(Panel(status_table, title="Telemetry Status"))

        # What we collect
        collected_table = Table(title="Data Collected (when enabled)", show_header=True)
        collected_table.add_column("Category", style="cyan")
        collected_table.add_column("Examples", style="white")

        collected_table.add_row("App Info", "version, mode (CLI/MCP)")
        collected_table.add_row("System Info", "OS, Python version, architecture")
        collected_table.add_row("Feature Usage", "MCP tools called, CLI commands used")
        collected_table.add_row("Performance", "sync duration, entity counts")
        collected_table.add_row("Errors", "error types (sanitized, no personal data)")

        console.print(collected_table)

        # What we never collect
        never_table = Table(title="Never Collected", show_header=True)
        never_table.add_column("Category", style="red")

        never_table.add_row("Note content or file contents")
        never_table.add_row("File names or paths")
        never_table.add_row("Personal information")
        never_table.add_row("IP addresses")

        console.print(never_table)

        # Commands
        console.print("\n[bold]Commands:[/bold]")
        console.print("  Enable:  [cyan]bm telemetry enable[/cyan]")
        console.print("  Disable: [cyan]bm telemetry disable[/cyan]")
        console.print("  Delete installation ID: [cyan]rm ~/.basic-memory/.install_id[/cyan]")
        console.print("\n[bold]More info:[/bold] https://memory.basicmachines.co/telemetry")

    except Exception as e:
        console.print(f"[red]Error getting telemetry status:[/red] {e}")
        raise typer.Exit(1)
