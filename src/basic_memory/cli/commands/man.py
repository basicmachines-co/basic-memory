"""`bm man`: read the bundled manual, and install the groff pages so `man bm` works."""

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Optional, override

import typer
from rich.console import Console
from typer.core import TyperGroup

# Typer vendors its own click; an override must be typed with the base class's
# types, and these are the ones TyperGroup.resolve_command is declared with.
from typer._click.core import Command, Context

from basic_memory.cli.app import app
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


@man_app.command()
def show(
    topic: Annotated[str, typer.Argument(help="Page name, e.g. search-notes or search-notes(3)")],
) -> None:
    """Print a manual page as Markdown."""
    try:
        page = find_page(parse_page_ref(topic))
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    if page is None:
        console.print(f"[red]No manual entry for {topic}[/red]  (try: bm man list)")
        raise typer.Exit(1)
    # Raw Markdown, unwrapped: agents and pagers read this as often as eyes do.
    sys.stdout.write(page.body())
    sys.stdout.write("\n")


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
