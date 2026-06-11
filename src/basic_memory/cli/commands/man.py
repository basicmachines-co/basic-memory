"""Install the bundled man pages so `man bm` works."""

import shutil
import subprocess
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from basic_memory.cli.app import app

console = Console()

man_app = typer.Typer(help="Manage the bm man pages.")
app.add_typer(man_app, name="man")

# Bundled groff sources ship inside the package (src/basic_memory/man).
_MAN_SOURCE_DIR = Path(__file__).parent.parent.parent / "man"


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
