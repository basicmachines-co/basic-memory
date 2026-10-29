"""Migration stubs for the cloud mirror commands removed in 1.0 (#1845).

`bm cloud sync`, `bm cloud bisync`, and `bm cloud bisync-reset` were deprecated
in #1596 and removed in 1.0. `sync` made Cloud match the local folder, deleting
cloud-only notes, including notes that people and agents created in Cloud.

The command names stay registered (hidden from help) so scripts and scheduled
jobs that still call them fail with directions instead of Click's generic
"No such command". The stubs read no config or credentials, call no API, and
touch no files. They do not forward to push or pull either: the right
direction and conflict choice depend on what the old job was for.
"""

from collections.abc import Sequence

import typer
from rich.console import Console

from basic_memory.cli.app import cloud_app
from basic_memory.config import resolve_data_dir
from basic_memory.utils import shell_command

console = Console()

# Trigger: an old invocation such as `bm cloud bisync --name research --resync`.
# Why: the stubs declare no options, so the flags of the removed commands would
#   otherwise fail with a usage error that hides the migration guidance.
# Outcome: every argument is accepted and ignored, and the stub explains the change.
ACCEPT_ANY_ARGS = {"allow_extra_args": True, "ignore_unknown_options": True}

PROJECT_PLACEHOLDER = "<project>"


def _project_name(args: Sequence[str]) -> str:
    """Pick the project name out of an old invocation, so the printed commands fit it."""
    for index, arg in enumerate(args):
        if arg in ("--name", "--project") and index + 1 < len(args):
            return args[index + 1]
        for option in ("--name=", "--project="):
            if arg.startswith(option):
                return arg.removeprefix(option)
    # `bisync-reset` took the project as a positional argument.
    positional = [arg for arg in args if not arg.startswith("-")]
    return positional[0] if positional else PROJECT_PLACEHOLDER


def _command(*parts: str) -> str:
    """Quote a real project name; leave the placeholder readable."""
    if parts[-1] == PROJECT_PLACEHOLDER:
        return f"{shell_command(*parts[:-1])} {PROJECT_PLACEHOLDER}"
    return shell_command(*parts)


def _exit_with_guidance(command: str, reason: str, name: str) -> None:
    """Print the migration guidance for a removed command and exit nonzero."""
    pull = _command("bm", "cloud", "pull", "--name", name)
    push = _command("bm", "cloud", "push", "--name", name)
    guidance = "\n".join(
        [
            f"`bm cloud {command}` was removed in Basic Memory 1.0. {reason}",
            "Nothing was transferred or deleted.",
            "",
            "Preview, then download cloud changes:",
            f"  {pull} --dry-run",
            f"  {pull}",
            "Preview, then upload local changes:",
            f"  {push} --dry-run",
            f"  {push}",
            "",
            "push and pull never delete files on the destination, and renames are not",
            "propagated as moves. If a file differs on both sides, the transfer stops",
            "before copying anything. Re-run with --on-conflict keep-local, keep-cloud,",
            "or keep-both to choose. `push --on-conflict keep-local` gives local content",
            "precedence but keeps files that exist only in Cloud.",
        ]
    )
    # markup=False: the project name is user text, and Rich markup in it would
    # change the commands the user copies.
    console.print(guidance, style="yellow", markup=False)
    raise typer.Exit(1)


@cloud_app.command("sync", hidden=True, context_settings=ACCEPT_ANY_ARGS)
def sync_removed(ctx: typer.Context) -> None:
    """Removed in 1.0. Use `bm cloud push` / `bm cloud pull`."""
    _exit_with_guidance(
        "sync",
        "It mirrored the local folder onto Cloud and deleted cloud-only files.",
        _project_name(ctx.args),
    )


@cloud_app.command("bisync", hidden=True, context_settings=ACCEPT_ANY_ARGS)
def bisync_removed(ctx: typer.Context) -> None:
    """Removed in 1.0. Use `bm cloud pull` / `bm cloud push`."""
    _exit_with_guidance(
        "bisync",
        "It mirrored both directions and could delete or overwrite files on either side.",
        _project_name(ctx.args),
    )


@cloud_app.command("bisync-reset", hidden=True, context_settings=ACCEPT_ANY_ARGS)
def bisync_reset_removed(ctx: typer.Context) -> None:
    """Removed in 1.0. push and pull keep no sync state."""
    state_dir = resolve_data_dir() / "bisync-state"
    _exit_with_guidance(
        "bisync-reset",
        "push and pull keep no sync state, so there is nothing to reset. Old bisync "
        f"state in {state_dir} is no longer used; you can delete it yourself.",
        _project_name(ctx.args),
    )
