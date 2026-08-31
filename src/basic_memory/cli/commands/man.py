"""`bm man`: read the bundled manual, and install the groff pages so `man bm` works."""

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any, Optional, override

import typer
from rich.console import Console
from rich.markup import escape as markup_escape
from typer.core import TyperGroup

# Typer vendors its own click; an override must be typed with the base class's
# types, and these are the ones TyperGroup.resolve_command is declared with.
from typer._click.core import Command, Context

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.cli.commands.routing import force_routing, validate_routing_flags
from basic_memory.cli.commands.tool import (
    _display_search_results,
    _plain_search_results,
    _print_json,
    _resolve_output_mode,
    _validate_output_flags,
)
from basic_memory.man import bundled_pages, find_page, parse_page_ref

console = Console()


class ManGroup(TyperGroup):
    """Let `bm man <topic>` read like man(1).

    A first argument that is not a subcommand is a page name, so `bm man search-notes`
    is `bm man show search-notes` without the ceremony. Real subcommands (`install`,
    `list`, `show`) and options keep their meaning.
    """

    @override
    def resolve_command(
        self, ctx: Context, args: list[str]
    ) -> tuple[str | None, Command | None, list[str]]:
        if args and not args[0].startswith("-") and self.get_command(ctx, args[0]) is None:
            args = ["show", *args]
        return super().resolve_command(ctx, args)


man_app = typer.Typer(help="Read the Basic Memory manual, or install the man pages.", cls=ManGroup)
app.add_typer(man_app, name="man")

# Bundled groff sources ship inside the package (src/basic_memory/man).
_MAN_SOURCE_DIR = Path(__file__).parent.parent.parent / "man"


def _show_manual_note_fallback(topic: str, project: Optional[str]) -> str:
    """Read a non-bundled topic as a note from the manual project via the man tool.

    Raises typer.Exit(1) with the man(1)-style miss message when the topic
    resolves nowhere. A ToolError is the tool's own "No manual entry" miss; a
    ValueError or RuntimeError means the manual project itself is unreachable
    (not configured locally, or cloud routing without credentials) — for a
    reader that is the same outcome, so it degrades to the same hint rather
    than a traceback (mirroring the man tool's "the hint is the useful error"
    decision).
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import man as mcp_man
    from fastmcp.exceptions import ToolError

    try:
        result = run_with_cleanup(mcp_man(page=topic, project=project))
    except (ToolError, ValueError, RuntimeError) as error:
        console.print(f"[red]No manual entry for {markup_escape(topic)}[/red]  (try: bm man list)")
        raise typer.Exit(1) from error
    return str(result)


@man_app.command()
def show(
    topic: Annotated[str, typer.Argument(help="Page name, e.g. search-notes or search-notes(3)")],
    project: Annotated[
        Optional[str],
        typer.Option(help="Manual project for non-bundled topics (default: manual)"),
    ] = None,
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
) -> None:
    """Print a manual page as Markdown."""
    try:
        validate_routing_flags(local, cloud)
    except ValueError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1) from error

    # Bundled pages resolve without the MCP stack or a database, so a broken
    # local install can never block reading the shipped docs (the reason `man`
    # sits in app.py's skip_init_commands). Only a bundled miss goes further.
    try:
        page = find_page(parse_page_ref(topic))
    except ValueError:
        # An unparseable reference may still name a manual note ("docs/foo"),
        # mirroring the man tool's note fallback for the same input.
        page = None

    if page is not None:
        # Raw Markdown, unwrapped: agents and pagers read this as often as eyes do.
        sys.stdout.write(page.body())
        sys.stdout.write("\n")
        return

    with force_routing(local=local, cloud=cloud):
        body = _show_manual_note_fallback(topic, project)
    sys.stdout.write(body)
    sys.stdout.write("\n")


def _apropos_manual_search(query: str, project: Optional[str]) -> str | dict[str, Any]:
    """Search the manual project's manpage notes via the man tool.

    Raises typer.Exit(1) with a man-specific hint when the manual project is
    unreachable (not configured locally, or cloud routing without
    credentials) — the raw routing error never mentions the manual project
    apropos actually searches, so on a fresh local install it reads as a
    demand for cloud credentials the user never asked for. Unlike show's
    fallback, the underlying error text still prints: for a genuinely
    cloud-configured install its setup hint is the real fix.
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import man as mcp_man
    from fastmcp.exceptions import ToolError

    try:
        return run_with_cleanup(mcp_man(query=query, project=project))
    except (ToolError, ValueError, RuntimeError) as error:
        manual_project = markup_escape(project or "manual")
        console.print(
            f"[red]apropos searches the '{manual_project}' project, which is not "
            f"reachable[/red]  (bundled pages: bm man list)"
        )
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1) from error


@man_app.command()
def apropos(
    query: Annotated[str, typer.Argument(help="Search text over manual pages")],
    project: Annotated[
        Optional[str],
        typer.Option(help="Manual project to search (default: manual)"),
    ] = None,
    json_output: bool = typer.Option(
        False, "--json", help="Output raw JSON instead of formatted display"
    ),
    plain: bool = typer.Option(
        False, "--plain", help="Output undecorated plain text (no colors/markup), even when piped"
    ),
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
) -> None:
    """Search the manual project's pages (`bm man list` indexes the bundled pages).

    Examples:

    bm man apropos "conflict resolution"
    bm man apropos sync --json
    """
    try:
        validate_routing_flags(local, cloud)
        _validate_output_flags(json_output, plain)
    except ValueError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1) from error

    with force_routing(local=local, cloud=cloud):
        result = _apropos_manual_search(query, project)

    # Query mode returns the search-notes response shape; empty results are
    # a successful search (exit 0), rendered as the "No results" panel. The
    # man tool's page mode returns a string — its signature admits one, and
    # a string has no structured shape to format — so that falls back to
    # JSON regardless of mode (read-note precedent).
    mode = _resolve_output_mode(json_output, plain)
    if mode == "json" or isinstance(result, str):
        _print_json(result)
    elif mode == "plain":
        _plain_search_results(result, query=query)
    else:
        _display_search_results(result, query=query)


@man_app.command(name="list")
def list_pages() -> None:
    """List every manual page with its one-line summary (apropos)."""
    for page in bundled_pages():
        sys.stdout.write(f"{page.title:<28} {page.summary}\n")


def _default_man_root() -> Path:
    # Why ~/.local/share/man: manpath(1) derives man directories from PATH
    # entries on both man-db (Linux) and BSD man (macOS), so ~/.local/bin on
    # PATH — the pipx/uv tool layout — makes this root searchable without any
    # MANPATH configuration.
    return Path.home() / ".local" / "share" / "man"


def _man_root_on_manpath(man_root: Path) -> Optional[bool]:
    """Best-effort check whether man(1) will search man_root; None if unknown."""
    try:
        result = subprocess.run(["manpath"], capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    paths = [entry.rstrip("/") for entry in result.stdout.strip().split(":") if entry]
    return str(man_root).rstrip("/") in paths


@man_app.command()
def install(
    directory: Annotated[
        Optional[Path],
        typer.Option(
            "--dir",
            help="Man root to install into (default: ~/.local/share/man)",
        ),
    ] = None,
) -> None:
    """Install the bm man pages, then try `man bm`."""
    man_root = (directory or _default_man_root()).expanduser()
    man1 = man_root / "man1"
    man1.mkdir(parents=True, exist_ok=True)

    pages = sorted(_MAN_SOURCE_DIR.glob("*.1"))
    if not pages:  # pragma: no cover - broken packaging, not a runtime state
        console.print("[red]No bundled man pages found — broken installation[/red]")
        raise typer.Exit(1)

    for page in pages:
        shutil.copyfile(page, man1 / page.name)
        console.print(f"installed {man1 / page.name}")

    # Trigger: the chosen root is provably absent from manpath output.
    # Why: a silent install into an unsearched directory looks like success
    #   but `man bm` still fails; say so and hand over the one-line fix.
    # Outcome: actionable hint; unknown (None) stays quiet to avoid false alarms.
    if _man_root_on_manpath(man_root) is False:
        console.print(
            f"\n[yellow]{man_root} is not on your manpath.[/yellow] Add it with:\n"
            f'  export MANPATH="{man_root}:$MANPATH"'
        )

    console.print("\nTry: [bold]man bm[/bold]")
