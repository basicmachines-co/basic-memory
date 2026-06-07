"""Top-level `workspace` stub that redirects users to `bm cloud workspace`."""

import typer

from basic_memory.cli.app import app


# Trigger: user runs `bm workspace`, `bm workspace list`, etc.
# Why: workspace verbs live under `bm cloud workspace`; a bare top-level miss
#   only emits Typer's terse "No such command 'workspace'." with exit 2.
# Outcome: allow_extra_args + ignore_unknown_options absorb any trailing tokens
#   (e.g. `list`, `set-default foo`) so every invocation reaches this body and
#   prints actionable guidance instead of being rejected as bad usage.
@app.command(
    "workspace",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def workspace_stub(ctx: typer.Context) -> None:
    """Point users to the real workspace verbs under `bm cloud workspace`."""
    typer.echo("'bm workspace' is not a command. Workspace verbs live under 'bm cloud workspace':")
    typer.echo("  bm cloud workspace list")
    typer.echo("  bm cloud workspace set-default <name>")
    raise typer.Exit(1)
