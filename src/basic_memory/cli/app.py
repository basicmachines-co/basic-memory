# Suppress Logfire "not configured" warning - we only use Logfire in cloud/server contexts
import os

os.environ.setdefault("LOGFIRE_IGNORE_NO_CONFIG", "1")

# Remove loguru's default handler IMMEDIATELY, before any other imports.
# This prevents DEBUG logs from appearing on stdout during module-level
# initialization (e.g., template_loader.TemplateLoader() logs at DEBUG level).
from loguru import logger

logger.remove()

from typing import Optional  # noqa: E402

import typer  # noqa: E402

from basic_memory.config import ConfigManager, init_cli_logging  # noqa: E402
from basic_memory.telemetry import show_notice_if_needed, track_app_started  # noqa: E402


def version_callback(value: bool) -> None:
    """Show version and exit."""
    if value:  # pragma: no cover
        import basic_memory

        typer.echo(f"Basic Memory version: {basic_memory.__version__}")
        raise typer.Exit()


app = typer.Typer(name="basic-memory")


@app.callback()
def app_callback(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """Basic Memory - Local-first personal knowledge management."""

    # Initialize logging for CLI (file only, no stdout)
    init_cli_logging()

    # Show telemetry notice and track CLI startup
    # Skip for 'mcp' command - it handles its own telemetry in lifespan
    # Skip for 'telemetry' command - avoid issues when user is managing telemetry
    if ctx.invoked_subcommand not in {"mcp", "telemetry"}:
        show_notice_if_needed()
        track_app_started("cli")

    # Run initialization for commands that don't use the API
    # Skip for 'mcp' command - it has its own lifespan that handles initialization
    # Skip for API-using commands (status, sync, etc.) - they handle initialization via deps.py
    api_commands = {"mcp", "status", "sync", "project", "tool"}
    if (
        not version
        and ctx.invoked_subcommand is not None
        and ctx.invoked_subcommand not in api_commands
    ):
        from basic_memory.services.initialization import ensure_initialization

        app_config = ConfigManager().config
        ensure_initialization(app_config)


## import
# Register sub-command groups
import_app = typer.Typer(help="Import data from various sources")
app.add_typer(import_app, name="import")

claude_app = typer.Typer(help="Import Conversations from Claude JSON export.")
import_app.add_typer(claude_app, name="claude")


## cloud

cloud_app = typer.Typer(help="Access Basic Memory Cloud")
app.add_typer(cloud_app, name="cloud")
