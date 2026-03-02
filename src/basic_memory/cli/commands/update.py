"""Manual update command for Basic Memory CLI."""

import typer
from rich.console import Console

from basic_memory.cli.app import app
from basic_memory.cli.auto_update import AutoUpdateStatus, run_auto_update

console = Console()


@app.command("update")
def update(
    check: bool = typer.Option(
        False,
        "--check",
        help="Check for updates only (do not install).",
    ),
) -> None:
    """Check for updates and install when supported."""
    result = run_auto_update(force=True, check_only=check, silent=False)

    if result.status == AutoUpdateStatus.FAILED:
        detail = f" {result.error}" if result.error else ""
        console.print(f"[red]{result.message or 'Update failed.'}{detail}[/red]")
        raise typer.Exit(1)

    if result.status == AutoUpdateStatus.UPDATED:
        console.print(f"[green]{result.message or 'Basic Memory updated successfully.'}[/green]")
        return

    if result.status == AutoUpdateStatus.UP_TO_DATE:
        console.print(f"[green]{result.message or 'Basic Memory is up to date.'}[/green]")
        return

    if result.status == AutoUpdateStatus.UPDATE_AVAILABLE:
        console.print(f"[cyan]{result.message or 'Update available.'}[/cyan]")
        return

    console.print(f"[dim]{result.message or 'No update action was performed.'}[/dim]")
