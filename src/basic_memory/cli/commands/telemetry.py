"""Telemetry commands for basic-memory CLI."""

import typer
from rich.console import Console
from rich.panel import Panel

from basic_memory.cli.app import app
from basic_memory.config import ConfigManager

console = Console()

# Create telemetry subcommand group
telemetry_app = typer.Typer(help="Manage anonymous telemetry settings")
app.add_typer(telemetry_app, name="telemetry")


@telemetry_app.command("enable")
def enable() -> None:
    """Enable anonymous telemetry.

    Telemetry helps improve Basic Memory by collecting anonymous usage data.
    No personal data, note content, or file paths are ever collected.
    """
    config_manager = ConfigManager()
    config = config_manager.config
    config.telemetry_enabled = True
    config_manager.save_config(config)
    console.print("[green]Telemetry enabled[/green]")
    console.print("[dim]Thank you for helping improve Basic Memory![/dim]")


@telemetry_app.command("disable")
def disable() -> None:
    """Disable anonymous telemetry.

    You can re-enable telemetry anytime with: bm telemetry enable
    """
    config_manager = ConfigManager()
    config = config_manager.config
    config.telemetry_enabled = False
    config_manager.save_config(config)
    console.print("[yellow]Telemetry disabled[/yellow]")


@telemetry_app.command("status")
def status() -> None:
    """Show current telemetry status and what's collected."""
    from basic_memory.telemetry import get_install_id, TELEMETRY_DOCS_URL

    config = ConfigManager().config

    status_text = (
        "[green]enabled[/green]" if config.telemetry_enabled else "[yellow]disabled[/yellow]"
    )

    console.print(f"\nTelemetry: {status_text}")
    console.print(f"Install ID: [dim]{get_install_id()}[/dim]")
    console.print()

    what_we_collect = """
[bold]What we collect:[/bold]
  - App version, Python version, OS, architecture
  - Feature usage (which MCP tools and CLI commands)
  - Sync statistics (entity count, duration)
  - Error types (sanitized, no file paths)

[bold]What we NEVER collect:[/bold]
  - Note content, file names, or paths
  - Personal information
  - IP addresses
"""

    console.print(
        Panel(
            what_we_collect.strip(),
            title="Telemetry Details",
            border_style="blue",
            expand=False,
        )
    )
    console.print(f"[dim]Details: {TELEMETRY_DOCS_URL}[/dim]")
