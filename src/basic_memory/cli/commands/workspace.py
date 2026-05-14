"""Top-level workspace stub — redirects users to 'bm cloud workspace'."""

import typer

from basic_memory.cli.app import app


@app.command(
    "workspace",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def workspace_redirect() -> None:
    """'bm workspace' is not a command — see 'bm cloud workspace'."""
    typer.echo("'bm workspace' is not a command. Workspace verbs live under 'bm cloud workspace':")
    typer.echo("  bm cloud workspace list")
    typer.echo("  bm cloud workspace set-default <name>")
    raise typer.Exit(1)
