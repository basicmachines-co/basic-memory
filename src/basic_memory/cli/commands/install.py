"""Install packaged host resources without initializing Basic Memory's database."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import distribution
from pathlib import Path
from typing import Literal

import typer

from basic_memory.cli.app import app
from basic_memory.utils import shell_command


class InstallError(ValueError):
    """Safe installer diagnostic containing no file contents or subprocess output."""


install_app = typer.Typer(help="Install Basic Memory resources into an agent host.")
app.add_typer(install_app, name="install")


@dataclass(frozen=True, slots=True)
class HostPlugin:
    """A plugin install delegated to an agent host's own marketplace CLI."""

    executable: str
    display: str
    installer: str
    summary: str
    steps: tuple[tuple[str, ...], ...]
    next_steps: tuple[str, ...]


def delegate_install(host: HostPlugin, *, dry_run: bool, yes: bool) -> None:
    """Run a host's own plugin commands, without initializing Basic Memory.

    The host owns every message, so a preview cannot reach the lines that only
    hold once the plugin is actually installed.
    """
    typer.echo(host.summary)
    for step in host.steps:
        typer.echo(shell_command(host.executable, *step))
    if dry_run:
        return
    executable = shutil.which(host.executable)
    if executable is None:
        typer.echo(f"{host.display} CLI not found on PATH. Install {host.display} first.", err=True)
        raise typer.Exit(1)
    if not yes and not typer.confirm("Apply this installation plan?", default=False):
        raise typer.Abort()
    for step in host.steps:
        try:
            result = subprocess.run([executable, *step], check=False)
        except OSError:
            typer.echo(
                f"Cannot launch {host.display} CLI. Check its installation and permissions.",
                err=True,
            )
            raise typer.Exit(1) from None
        # A failed marketplace registration must not install from an unrelated
        # previously configured source with the same marketplace name.
        if result.returncode:
            typer.echo(
                f"{host.display} command failed: "
                + shell_command(host.executable, *step)
                + f". Resolve the error above and rerun {host.installer}.",
                err=True,
            )
            raise typer.Exit(1)
    for line in host.next_steps:
        typer.echo(line)


@install_app.command("codex")
def install_codex(
    source: str = typer.Option(
        "basicmachines-co/basic-memory",
        "--source",
        help="Marketplace Git source or local repo root.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview only; no subprocesses."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Approve the displayed installation plan."),
) -> None:
    """Install the Basic Memory plugin through Codex's user-level marketplace."""
    host = HostPlugin(
        executable="codex",
        display="Codex",
        installer="bm install codex",
        summary="Install the Basic Memory marketplace and plugin into Codex (user-level).",
        steps=(
            ("plugin", "marketplace", "add", source),
            ("plugin", "add", "codex@basic-memory"),
        ),
        next_steps=(
            "Basic Memory plugin installed. Start a new Codex thread and run $bm-setup.",
            "Open /hooks in Codex to review and trust the Basic Memory hooks (requires uv).",
        ),
    )
    delegate_install(host, dry_run=dry_run, yes=yes)


class InstallScope(StrEnum):
    """Claude Code's scopes for declaring a marketplace and installing a plugin."""

    user = "user"
    project = "project"
    local = "local"


@install_app.command("claude-code")
def install_claude_code(
    source: str = typer.Option(
        "basicmachines-co/basic-memory",
        "--source",
        help="Marketplace Git source or local repo root.",
    ),
    scope: InstallScope = typer.Option(
        InstallScope.user,
        "--scope",
        help="Where Claude Code declares the marketplace and plugin.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview only; no subprocesses."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Approve the displayed installation plan."),
) -> None:
    """Install the Basic Memory plugin through Claude Code's marketplace."""
    add = ["plugin", "marketplace", "add", source, "--scope", scope.value]
    # Trigger: --source names a checkout on this machine instead of a Git remote.
    # Why: --sparse configures a git sparse-checkout, and Claude Code rejects it
    # for directory sources. Outcome: remote installs fetch only the two paths
    # the plugin needs out of the monorepo; local installs read the checkout.
    if not Path(source).is_dir():
        add += ["--sparse", ".claude-plugin", "plugins/claude-code"]
    host = HostPlugin(
        executable="claude",
        display="Claude Code",
        installer="bm install claude-code",
        summary=(
            "Install the Basic Memory marketplace and plugin into Claude Code "
            f"({scope.value}-level)."
        ),
        steps=(
            tuple(add),
            ("plugin", "install", "basic-memory@basicmachines-co", "--scope", scope.value),
        ),
        next_steps=(
            "Basic Memory plugin installed. Restart Claude Code and run /basic-memory:bm-setup.",
            # The plugin ships no MCP server of its own, so an unconnected server
            # leaves every skill failing on its first tool call.
            "Its skills call the Basic Memory MCP server; connect it if you have not: "
            + shell_command(
                "claude",
                "mcp",
                "add",
                "basic-memory",
                "--",
                "uvx",
                "--prerelease=allow",
                "basic-memory",
                "mcp",
            ),
            "The hooks run through uv; install uv first if it is not already on PATH.",
        ),
    )
    delegate_install(host, dry_run=dry_run, yes=yes)


@dataclass(frozen=True, slots=True)
class InstallFile:
    path: Path
    content: bytes
    previous: bytes | None

    @property
    def action(self) -> Literal["create", "unchanged", "replace"]:
        if self.previous is None:
            return "create"
        return "unchanged" if self.previous == self.content else "replace"


def plan_tau_install(bundle: Path, home: Path, cwd: Path) -> tuple[list[InstallFile], list[str]]:
    """Build a read-only plan; existing shared skills win over bundled copies."""
    extension = home / ".tau/extensions/basic-memory"
    notices: list[str] = []
    pairs: list[tuple[Path, Path]] = []
    for source in sorted((bundle / "extension").rglob("*")):
        if source.is_file():
            pairs.append((source, extension / source.relative_to(bundle / "extension")))
    for source in sorted((bundle / "prompts").glob("*.md")):
        higher = [
            home / ".agents/prompts" / source.name,
            cwd / ".tau/prompts" / source.name,
            cwd / ".agents/prompts" / source.name,
        ]
        if any(path != home / ".tau/prompts" / source.name and path.exists() for path in higher):
            notices.append(
                f"Skip prompt {source.stem}: already available in another resource root."
            )
            continue
        pairs.append((source, home / ".tau/prompts" / source.name))
    for skill in sorted((bundle / "skills").iterdir()):
        higher = [
            home / ".agents/skills" / skill.name / "SKILL.md",
            cwd / ".tau/skills" / skill.name / "SKILL.md",
            cwd / ".agents/skills" / skill.name / "SKILL.md",
        ]
        if any(
            path != home / ".tau/skills" / skill.name / "SKILL.md" and path.exists()
            for path in higher
        ):
            notices.append(f"Skip skill {skill.name}: already available in another resource root.")
            continue
        for source in sorted(skill.rglob("*")):
            if source.is_file():
                pairs.append(
                    (source, home / ".tau/skills" / skill.name / source.relative_to(skill))
                )
    plan: list[InstallFile] = []
    for source, target in pairs:
        # User-owned symlinks are not permission to write into their targets.
        if any(
            path.is_symlink() for path in (target, *target.parents) if path.is_relative_to(home)
        ):
            raise InstallError("A destination contains a symlink; resolve it before installing.")
        previous = target.read_bytes() if target.exists() else None
        plan.append(InstallFile(target, source.read_bytes(), previous))
    if not (bundle / "extension/pyproject.toml").is_file() or not plan:
        raise InstallError("Packaged Tau resources are missing; reinstall Basic Memory.")
    return plan, notices


def apply_tau_install(plan: list[InstallFile]) -> None:
    """Publish approved files privately, refusing drift since the preview."""
    for item in plan:
        current = item.path.read_bytes() if item.path.exists() else None
        if item.path.is_symlink() or current != item.previous:
            raise InstallError("A destination changed after preview; rerun the installer.")
    for item in plan:
        if item.action == "unchanged":
            continue
        item.path.parent.mkdir(parents=True, exist_ok=True)
        if item.previous is None:
            descriptor = os.open(item.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(item.content)
        else:
            mode = item.path.stat().st_mode & 0o777
            with tempfile.NamedTemporaryFile(dir=item.path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(item.content)
            try:
                temporary.chmod(mode)
                temporary.replace(item.path)
            finally:
                temporary.unlink(missing_ok=True)


def other_tau_copies(home: Path, target: Path) -> bool:
    """Recognize this package's manifest without inspecting unrelated host config."""
    for manifest in (home / ".tau/extensions").glob("*/pyproject.toml"):
        if manifest.parent == target:
            continue
        try:
            data = tomllib.loads(manifest.read_text())
        except tomllib.TOMLDecodeError:
            raise InstallError(
                "An extension manifest is malformed; fix it before installing."
            ) from None
        project = data.get("project")
        if isinstance(project, dict) and project.get("name") == "basic-memory-tau":
            return True
    return False


@install_app.command("tau")
def install_tau(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview only; no writes or subprocesses."
    ),
    replace: bool = typer.Option(
        False, "--replace", help="Allow replacement of listed differing files."
    ),
    sync: bool = typer.Option(
        False, "--sync", help="Install the pinned isolated Tau dependencies using uv."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Approve the displayed file/dependency plan."
    ),
) -> None:
    """Install the extension, setup/shared skills, and four prompt templates.

    Existing config is never changed. Destination and automatic capture policy
    are chosen through /skill:basic-memory-setup after launching compatible Tau.
    """
    home = Path.home()
    target = home / ".tau/extensions/basic-memory"
    uv = shutil.which("uv")
    typer.echo(
        "Tau requires Python 3.13+ and the bundled immutable fork pin (stock 0.4.1 is insufficient)."
    )
    typer.echo(
        f"uv: {'available' if uv else 'not found'}; Tau on PATH: {'found' if shutil.which('tau') else 'not found'}"
    )
    typer.echo(
        "Existing config and credentials will not be read or changed. No notes will be written."
    )
    typer.echo(
        "Stop using an explicit source copy (-e) before launching the installed copy; do not load both."
    )
    try:
        if other_tau_copies(home, target):
            raise InstallError(
                "Another Basic Memory extension copy exists; choose one install before continuing."
            )
        # Wheel data is distribution-owned. Editable installs import Python from
        # src/ but Hatch still places these resources beside the installed metadata.
        bundle = distribution("basic-memory").locate_file("basic_memory/data/tau")
        if not isinstance(bundle, Path):
            raise InstallError("Tau installation requires an unpacked Basic Memory distribution.")
        plan, notices = plan_tau_install(bundle, home, Path.cwd())
        for notice in notices:
            typer.echo(notice)
        for item in plan:
            typer.echo(f"{item.action}: {item.path}")
        if sync:
            typer.echo("May download Python 3.13+ and dependencies into an isolated environment.")
            typer.echo(
                "Dependency installation: "
                + shell_command("uv", "sync", "--project", str(target), "--no-dev", "--frozen")
            )
        if dry_run:
            return
        if any(item.action == "replace" for item in plan) and not replace:
            raise InstallError(
                "Differing files preserved. Review them and rerun with --replace to approve replacements."
            )
        if sync and uv is None:
            raise InstallError("uv is required for --sync; install uv explicitly first.")
        if not yes and not typer.confirm("Apply this installation plan?", default=False):
            raise typer.Abort()
        apply_tau_install(plan)
        if sync:
            assert uv is not None
            result = subprocess.run(
                [uv, "sync", "--project", str(target), "--no-dev", "--frozen"],
                capture_output=True,
                check=False,
            )
            if result.returncode:
                raise InstallError(
                    "Resources installed, but isolated dependency installation failed. Raw output withheld; inspect uv separately."
                )
    except (OSError, ValueError) as exc:
        # Paths/arguments and dependency stderr may contain private values.
        message = (
            str(exc)
            if isinstance(exc, InstallError)
            else "Cannot read/write installation resources. Check permissions and package contents."
        )
        typer.echo(message, err=True)
        raise typer.Exit(1) from None
    typer.echo(
        "Resources installed. Dependency environment: "
        + ("synced" if sync else "not verified (run with --sync)")
    )
    typer.echo("Launch: " + shell_command("uv", "run", "--project", str(target), "tau"))
    typer.echo(
        "Then /skill:basic-memory-setup to choose destination/profile/policy, followed by /bm-status."
    )
    typer.echo(
        "Existing config may enable automatic capture. Reload shuts down the old lifecycle and may save using its old settings."
    )
    typer.echo("Connection, recall, and continuity are unverified; no Tau session was started.")
