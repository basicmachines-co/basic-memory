"""Automatic update checks and upgrades for the Basic Memory CLI."""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from loguru import logger
from packaging.version import InvalidVersion, Version
from rich.console import Console

import basic_memory
from basic_memory.config import ConfigManager

PACKAGE_NAME = "basic-memory"
PYPI_JSON_URL = "https://pypi.org/pypi/basic-memory/json"

PYPI_TIMEOUT_SECONDS = 5
BREW_OUTDATED_TIMEOUT_SECONDS = 60
UV_UPGRADE_TIMEOUT_SECONDS = 180
BREW_UPGRADE_TIMEOUT_SECONDS = 600


class HomebrewCheckError(RuntimeError):
    """Raised when `brew outdated` could not determine whether an update exists."""


class InstallSource(str, Enum):
    """How the running CLI appears to have been installed."""

    HOMEBREW = "homebrew"
    UV_TOOL = "uv_tool"
    UVX = "uvx"
    UNKNOWN = "unknown"


class AutoUpdateStatus(str, Enum):
    """Result classification for update checks and installs."""

    SKIPPED = "skipped"
    UP_TO_DATE = "up_to_date"
    UPDATE_AVAILABLE = "update_available"
    UPDATED = "updated"
    FAILED = "failed"


@dataclass(frozen=True)
class AutoUpdateResult:
    """Structured result for update checks/install attempts."""

    status: AutoUpdateStatus
    source: InstallSource
    checked: bool
    update_available: bool
    updated: bool
    latest_version: str | None = None
    message: str | None = None
    error: str | None = None
    restart_recommended: bool = False


def detect_install_source(executable: str | None = None) -> InstallSource:
    """Infer installation source from the active interpreter path."""
    active_executable = executable or sys.executable
    normalized = active_executable.lower().replace("\\", "/")

    if "cellar/basic-memory" in normalized:
        return InstallSource.HOMEBREW
    if "uv/tools/basic-memory" in normalized:
        return InstallSource.UV_TOOL
    if "/uv/archive-" in normalized:
        return InstallSource.UVX
    return InstallSource.UNKNOWN


def _is_interactive_session() -> bool:
    """Return whether stdin/stdout are interactive terminals."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except ValueError:
        # Trigger: stdin/stdout may be closed during transport teardown.
        # Why: isatty() raises ValueError on closed descriptors.
        # Outcome: treat as non-interactive and suppress periodic output.
        return False


def _run_subprocess(
    command: list[str],
    *,
    timeout_seconds: int,
    silent: bool,
    capture_output: bool,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with explicit stdio behavior for protocol safety."""
    # Trigger: silent operation (MCP/background) with no need for subprocess output.
    # Why: prevent protocol/terminal pollution from child process output.
    # Outcome: stdout/stderr are discarded unless explicit capture is requested.
    use_devnull = silent and not capture_output
    stdout_target = subprocess.DEVNULL if use_devnull else subprocess.PIPE
    stderr_target = subprocess.DEVNULL if use_devnull else subprocess.PIPE

    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=stdout_target,
        stderr=stderr_target,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )


def _version_from_pypi() -> str:
    """Fetch the latest published package version from PyPI."""
    request = urllib.request.Request(
        PYPI_JSON_URL,
        headers={"User-Agent": f"basic-memory-cli/{basic_memory.__version__}"},
    )
    with urllib.request.urlopen(request, timeout=PYPI_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    latest = payload.get("info", {}).get("version")
    if not latest:
        raise RuntimeError("PyPI JSON response did not include info.version")
    return str(latest)


def _check_homebrew_update_available(silent: bool) -> tuple[bool, str | None]:
    """Check whether Homebrew reports an outdated basic-memory formula.

    Raises:
        HomebrewCheckError: brew could not answer the question (brew missing,
            untrusted or stale tap, network failure, timeout).
    """
    try:
        result = _run_subprocess(
            ["brew", "outdated", "--json=v2", PACKAGE_NAME],
            timeout_seconds=BREW_OUTDATED_TIMEOUT_SECONDS,
            silent=silent,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HomebrewCheckError(f"could not run `brew outdated`: {exc}") from exc

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise HomebrewCheckError(
            stderr or f"`brew outdated --json=v2` returned invalid JSON: {exc}"
        ) from exc

    formulae = payload.get("formulae") if isinstance(payload, dict) else None
    if not isinstance(formulae, list):
        raise HomebrewCheckError("`brew outdated --json=v2` omitted the formulae list")

    # Trigger: brew outdated exits 1 both when the formula IS outdated (a JSON
    # entry is present) and when the check failed outright (non-JSON error text).
    # Why: JSON supplies Homebrew's actual target version without consulting a
    # potentially-ahead package registry, while the exit code alone is ambiguous.
    # Outcome: a matching formula is outdated, an empty successful list is current,
    # and every other response remains unanswered for the caller's fallback path.
    for formula in formulae:
        if not isinstance(formula, dict):
            raise HomebrewCheckError("`brew outdated --json=v2` returned a malformed formula")
        name = formula.get("name")
        if isinstance(name, str) and name.rsplit("/", maxsplit=1)[-1] == PACKAGE_NAME:
            latest = formula.get("current_version")
            if not isinstance(latest, str) or not latest:
                raise HomebrewCheckError(
                    "`brew outdated --json=v2` omitted the formula current_version"
                )
            return True, latest

    if result.returncode == 0 and not formulae:
        return False, None
    raise HomebrewCheckError(stderr or f"`brew outdated` exited {result.returncode}")


def _check_pypi_update_available() -> tuple[bool, str]:
    """Compare installed package version with PyPI latest version."""
    latest = _version_from_pypi()
    try:
        current_version = Version(basic_memory.__version__)
        latest_version = Version(latest)
    except InvalidVersion as exc:
        raise RuntimeError(
            f"Could not compare versions (current={basic_memory.__version__}, latest={latest})"
        ) from exc

    return latest_version > current_version, latest


def _manual_update_hint(source: InstallSource) -> str:
    """Return manager-appropriate manual update instructions."""
    if source == InstallSource.UV_TOOL:
        return "Run `uv tool upgrade basic-memory --prerelease=allow`."
    if source == InstallSource.HOMEBREW:
        return "Run `brew upgrade basic-memory`."
    return (
        "Automatic install is not supported for this environment. "
        "Update with your package manager (for pip: `python3 -m pip install -U basic-memory`)."
    )


def _preload_lazy_console_modules() -> None:
    """Import modules the post-upgrade output path defers until print time.

    Trigger: an in-place upgrade is about to replace this installation on disk.
    Why: rich and typer defer some imports until print/excepthook time; once
    `brew upgrade` / `uv tool upgrade` removes the running version's files,
    those imports raise ModuleNotFoundError and the final status message
    crashes the exiting process.
    Outcome: import the deferred modules now, while the files still exist.
    """
    import rich._emoji_codes  # noqa: F401
    import typer.rich_utils  # noqa: F401
    from rich.cells import cell_len

    # Trigger: rich defers its Unicode cell-width table (`rich._unicode_data.
    # unicode<version>`) until the first character it cannot measure with the
    # ASCII fast path in `_cell_len`.
    # Why: status messages echo captured `brew`/`uv` output, which carries
    # non-ASCII characters (curly quotes, em dashes, warning glyphs), so the
    # deferred import lands after the upgrade removed our files. Importing the
    # module by name would hard-code a table version; calling `cell_len` uses
    # rich's own resolution and honors UNICODE_VERSION like the print path does.
    # Outcome: the table rich will reach for is resolved and cached up front.
    cell_len("\u2500\u2018\u2713")


def print_update_status(console: Console, text: str, style: str) -> None:
    """Print an update status line that cannot fail the command.

    Trigger: the line is printed after an in-place upgrade may already have
    replaced this installation on disk.
    Why: `_preload_lazy_console_modules` can only preload the deferred imports
    we know about today, and rich/typer are free to add more. By the time this
    prints, the upgrade has already succeeded -- a status line must never be
    what turns it into a traceback and a non-zero exit.
    Outcome: fall back to a plain, unstyled write that needs no new imports.
    """
    try:
        console.print(f"[{style}]{text}[/{style}]")
    except Exception as exc:
        logger.warning(
            f"Rich console print failed after update, falling back to plain output: {exc}"
        )
        print(text)


def _save_last_checked_timestamp(config_manager: ConfigManager, checked_at: datetime) -> None:
    """Persist the timestamp for the most recent attempted update check."""
    config = config_manager.load_config()
    config.auto_update_last_checked_at = checked_at
    config_manager.save_config(config)


def run_auto_update(
    *,
    force: bool = False,
    check_only: bool = False,
    silent: bool = False,
    config_manager: ConfigManager | None = None,
    now: datetime | None = None,
    executable: str | None = None,
) -> AutoUpdateResult:
    """Run update check/install flow and return a structured result."""
    manager = config_manager or ConfigManager()
    config = manager.load_config()
    source = detect_install_source(executable)
    checked_at = now or datetime.now()

    if source == InstallSource.UVX:
        return AutoUpdateResult(
            status=AutoUpdateStatus.SKIPPED,
            source=source,
            checked=False,
            update_available=False,
            updated=False,
            message="uvx runtime detected; updates are managed by uvx cache resolution.",
        )

    if not force and not config.auto_update:
        return AutoUpdateResult(
            status=AutoUpdateStatus.SKIPPED,
            source=source,
            checked=False,
            update_available=False,
            updated=False,
            message="Auto-update is disabled in config.",
        )

    if not force and config.auto_update_last_checked_at is not None:
        try:
            elapsed = checked_at - config.auto_update_last_checked_at
        except TypeError:
            # Trigger: mixed naive/aware datetimes from manual config edits.
            # Why: datetime subtraction fails for mixed tz-awareness.
            # Outcome: ignore the gate once and continue with a forced check path.
            logger.warning("Auto-update interval gate skipped due to incompatible timestamp format")
        else:
            if elapsed < timedelta(seconds=config.update_check_interval):
                return AutoUpdateResult(
                    status=AutoUpdateStatus.SKIPPED,
                    source=source,
                    checked=False,
                    update_available=False,
                    updated=False,
                    message="Update check interval has not elapsed.",
                )

    try:
        # --- Availability check ---
        latest_version: str | None = None
        homebrew_check_error: str | None = None
        if source == InstallSource.HOMEBREW:
            try:
                update_available, latest_version = _check_homebrew_update_available(silent=silent)
            except HomebrewCheckError as exc:
                # Trigger: brew cannot answer (missing/untrusted tap, no brew, network).
                # Why: an unanswered check must never be reported as up to date. PyPI is
                # sound for the negative answer -- the tap can only lag PyPI, so "nothing
                # newer exists" holds. It is NOT sound for installing: release.yml
                # publishes to PyPI in `release`, and the homebrew formula job `needs:
                # release`, so a newer PyPI version may not be installable via brew yet.
                # Outcome: ask PyPI, but remember the answer came from there.
                homebrew_check_error = str(exc)
                logger.warning(f"Homebrew update check failed, falling back to PyPI: {exc}")
                update_available, latest_version = _check_pypi_update_available()
        else:
            update_available, latest_version = _check_pypi_update_available()

        if not update_available:
            return AutoUpdateResult(
                status=AutoUpdateStatus.UP_TO_DATE,
                source=source,
                checked=True,
                update_available=False,
                updated=False,
                latest_version=latest_version,
                message=f"Basic Memory is up to date ({basic_memory.__version__}).",
            )

        if check_only:
            return AutoUpdateResult(
                status=AutoUpdateStatus.UPDATE_AVAILABLE,
                source=source,
                checked=True,
                update_available=True,
                updated=False,
                latest_version=latest_version,
                message=(
                    f"Update available (latest: {latest_version or 'unknown'}). "
                    f"{_manual_update_hint(source)}"
                ),
            )

        if source == InstallSource.UNKNOWN:
            return AutoUpdateResult(
                status=AutoUpdateStatus.UPDATE_AVAILABLE,
                source=source,
                checked=True,
                update_available=True,
                updated=False,
                latest_version=latest_version,
                message=(
                    f"Update available (latest: {latest_version or 'unknown'}). "
                    f"{_manual_update_hint(source)}"
                ),
            )

        if homebrew_check_error is not None:
            # Trigger: availability was inferred from PyPI because brew could not answer.
            # Why: the tap may not carry this version yet, and whatever hid the brew
            # answer (untrusted tap, brew missing) will also block `brew upgrade`.
            # Outcome: report the update and the reason instead of running a doomed
            # upgrade; the user resolves the brew problem and upgrades deliberately.
            return AutoUpdateResult(
                status=AutoUpdateStatus.UPDATE_AVAILABLE,
                source=source,
                checked=True,
                update_available=True,
                updated=False,
                latest_version=latest_version,
                message=(
                    f"Update available (latest: {latest_version or 'unknown'}), but the "
                    f"Homebrew check failed: {homebrew_check_error} "
                    f"{_manual_update_hint(source)}"
                ),
            )

        # --- Automatic install ---
        # uv refuses the FastMCP pre-release basic-memory pins unless asked;
        # without the flag the upgrade silently resolves to the previous
        # release that had no pre-release dependencies (#1338).
        command = (
            ["uv", "tool", "upgrade", PACKAGE_NAME, "--prerelease=allow"]
            if source == InstallSource.UV_TOOL
            else ["brew", "upgrade", PACKAGE_NAME]
        )
        timeout = (
            UV_UPGRADE_TIMEOUT_SECONDS
            if source == InstallSource.UV_TOOL
            else BREW_UPGRADE_TIMEOUT_SECONDS
        )

        _preload_lazy_console_modules()
        install_result = _run_subprocess(
            command,
            timeout_seconds=timeout,
            silent=silent,
            capture_output=not silent,
        )
        if install_result.returncode != 0:
            stderr = (install_result.stderr or "").strip() if install_result.stderr else ""
            stdout = (install_result.stdout or "").strip() if install_result.stdout else ""
            detail = stderr or stdout or "update command failed"
            return AutoUpdateResult(
                status=AutoUpdateStatus.FAILED,
                source=source,
                checked=True,
                update_available=True,
                updated=False,
                latest_version=latest_version,
                message="Automatic update failed.",
                error=detail,
            )

        return AutoUpdateResult(
            status=AutoUpdateStatus.UPDATED,
            source=source,
            checked=True,
            update_available=True,
            updated=True,
            latest_version=latest_version,
            message=(
                "Basic Memory was updated successfully. "
                "Restart running sessions to use the new version."
            ),
            restart_recommended=True,
        )

    except (
        RuntimeError,
        urllib.error.URLError,
        ValueError,
        TimeoutError,
        subprocess.SubprocessError,
        OSError,
    ) as exc:
        logger.warning(f"Auto-update check failed: {exc}")
        return AutoUpdateResult(
            status=AutoUpdateStatus.FAILED,
            source=source,
            checked=True,
            update_available=False,
            updated=False,
            message="Automatic update check failed.",
            error=str(exc),
        )
    finally:
        # Trigger: we attempted a check path (including failures).
        # Why: repeated failing checks on every command create noise and unnecessary network load.
        # Outcome: next periodic check is gated by update_check_interval.
        try:
            _save_last_checked_timestamp(manager, checked_at)
        except Exception as exc:  # pragma: no cover
            logger.warning(f"Failed to persist auto-update timestamp: {exc}")


def maybe_run_periodic_auto_update(
    invoked_subcommand: str | None,
    *,
    config_manager: ConfigManager | None = None,
    is_interactive: bool | None = None,
    console: Console | None = None,
) -> AutoUpdateResult | None:
    """Run a periodic auto-update check for interactive CLI sessions."""
    interactive = _is_interactive_session() if is_interactive is None else is_interactive
    if not interactive:
        return None
    if invoked_subcommand in {None, "mcp", "update"}:
        return None

    result = run_auto_update(
        force=False,
        check_only=False,
        silent=False,
        config_manager=config_manager,
    )

    if result.status in {
        AutoUpdateStatus.UPDATE_AVAILABLE,
        AutoUpdateStatus.UPDATED,
        AutoUpdateStatus.FAILED,
    }:
        out = console or Console()
        if result.status == AutoUpdateStatus.UPDATED:
            print_update_status(out, f"{result.message}", "green")
        elif result.status == AutoUpdateStatus.FAILED:
            error_detail = f" {result.error}" if result.error else ""
            print_update_status(out, f"{result.message}{error_detail}", "yellow")
        elif result.message:
            print_update_status(out, f"{result.message}", "cyan")

    return result
