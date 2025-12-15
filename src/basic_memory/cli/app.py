from typing import Optional

import typer

from basic_memory.config import ConfigManager, init_cli_logging


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

    # Run initialization for every command unless --version was specified
    if not version and ctx.invoked_subcommand is not None:
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
