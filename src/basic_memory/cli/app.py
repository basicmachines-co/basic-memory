import asyncio
from typing import Optional

import typer

from basic_memory import db
from basic_memory.config import config


def version_callback(value: bool) -> None:
    """Show version and exit."""
    if value:  # pragma: no cover
        import basic_memory

        typer.echo(f"Basic Memory version: {basic_memory.__version__}")
        raise typer.Exit()


app = typer.Typer(name="basic-memory")


@app.callback()
def app_callback(
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
    # We use the project option to set the BASIC_MEMORY_PROJECT environment variable
    # The config module will pick this up when loading
    if project:  # pragma: no cover
        import os
        import importlib
        from basic_memory import config as config_module

        # Set the environment variable
        os.environ["BASIC_MEMORY_PROJECT"] = project

        # Reload the config module to pick up the new project
        importlib.reload(config_module)

        # Update the local reference
        global config
        from basic_memory.config import config as new_config

        config = new_config


# Run database migrations
asyncio.run(db.run_migrations(config))

# Register sub-command groups
import_app = typer.Typer(help="Import data from various sources")
app.add_typer(import_app, name="import")

claude_app = typer.Typer()
import_app.add_typer(claude_app, name="claude")
