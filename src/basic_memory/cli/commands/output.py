"""Output modes for `bm inspect`, and the shared Rich console.

`bm inspect` prints in one of three modes:

- **JSON**: raw machine-readable JSON, with ``--json`` or whenever stdout is not a
  terminal, so scripts stay parseable.
- **Rich**: colored panels and tables, the default in a terminal.
- **Plain**: undecorated, greppable text with ``--plain``, even when piped.

Precedence, highest first: ``--json`` > ``--plain`` > non-terminal (JSON) > terminal
(config ``cli_output_style``, ``rich`` by default). ``--json`` with ``--plain`` is an
error.
"""

import sys
from typing import Literal

import typer
from rich.console import Console

from basic_memory.config import ConfigManager

# Shared Rich console (stderr=False so output goes to stdout).
console = Console()


# --- Shared helpers ---

OutputMode = Literal["json", "rich", "plain"]


def stdout_is_terminal() -> bool:
    """Return True when stdout is an interactive TTY.

    Why: piped output (scripts, jq, etc.) must stay machine-parseable; the
         interactive (Rich/plain) renderers are only the default in a terminal.
    Outcome: a formatted renderer in a terminal; raw JSON when piped or redirected.

    Note: tests patch this to simulate a TTY, so the precedence logic in
    ``resolve_output_mode`` routes its terminal check through here.
    """
    return sys.stdout.isatty()


def validate_output_flags(json_output: bool, plain: bool) -> None:
    """Reject the contradictory --json/--plain combination.

    Trigger: both --json and --plain were passed.
    Why: they request mutually exclusive output modes (raw JSON vs undecorated
         human text); silently picking one would hide a user mistake.
    Outcome: a clear typer error with a non-zero exit.
    """
    if json_output and plain:
        typer.echo("Error: --json and --plain are mutually exclusive.", err=True)
        raise typer.Exit(1)


def resolve_output_mode(json_output: bool, plain: bool) -> OutputMode:
    """Resolve the effective output mode from flags, TTY state, and config.

    Precedence, highest first:
      1. --json            → raw JSON (wins over everything else)
      2. --plain           → undecorated plain text (even when piped)
      3. non-TTY stdout    → raw JSON (script compatibility, unchanged)
      4. TTY               → config ``cli_output_style`` (rich by default)

    Callers must invoke ``validate_output_flags`` first; this helper assumes the
    --json/--plain combination has already been rejected.
    """
    if json_output:
        return "json"
    if plain:
        return "plain"
    if not stdout_is_terminal():
        return "json"
    # Trigger: interactive TTY with no explicit mode flag.
    # Why: let users choose their default terminal experience without a flag.
    # Outcome: honor cli_output_style (rich out of the box, plain if configured).
    return ConfigManager().config.cli_output_style
