from typing import Optional

import typer

from basic_memory.config import get_project_config, ConfigManager


def version_callback(value: bool) -> None:
    """Show version and exit."""
    if value:  # pragma: no cover
        import basic_memory

        config = get_project_config()
        typer.echo(f"Basic Memory version: {basic_memory.__version__}")
        typer.echo(f"Current project: {config.project}")
        typer.echo(f"Project path: {config.home}")
        raise typer.Exit()


app = typer.Typer(name="basic-memory")


@app.callback()
def app_callback(
    ctx: typer.Context,
    project: Optional[str] = typer.Option(
        None,
        "--project",
        "-p",
        help="Specify which project to use",
        envvar="BASIC_MEMORY_PROJECT",
    ),
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

    # Run initialization for every command unless --version was specified
    if not version and ctx.invoked_subcommand is not None:
        from basic_memory.services.initialization import ensure_initialization

        app_config = ConfigManager().config
        ensure_initialization(app_config)

        # Initialize MCP session with the specified project or default
        if project:  # pragma: no cover
            # Update the global config to use this project
            from basic_memory.config import update_current_project

            # TODO set active project via cli
            update_current_project(project)


# Register sub-command groups
import_app = typer.Typer(help="Import data from various sources")
app.add_typer(import_app, name="import")

claude_app = typer.Typer()
import_app.add_typer(claude_app, name="claude")
