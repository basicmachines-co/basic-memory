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
BREW_OUTDATED_TIMEOUT_SECONDS = 15
UV_UPGRADE_TIMEOUT_SECONDS = 180
BREW_UPGRADE_TIMEOUT_SECONDS = 600


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
    if "/uv/archive-" in normalized or "/uv/archive-v" in normalized:
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
    # Trigger: caller needs command output for decision-making (e.g., brew outdated).
    # Why: update-availability checks parse command output.
    # Outcome: stdout/stderr are captured in-memory (never forwarded to terminal).
    if capture_output:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )

    if silent:
        # Trigger: silent operation (MCP/background) with no need for subprocess output.
        # Why: prevent protocol/terminal pollution from child process output.
        # Outcome: stdout/stderr are discarded.
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )

    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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
    """Check whether Homebrew reports an outdated basic-memory formula."""
    result = _run_subprocess(
        ["brew", "outdated", "--quiet", PACKAGE_NAME],
        timeout_seconds=BREW_OUTDATED_TIMEOUT_SECONDS,
        silent=silent,
        capture_output=True,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or "brew outdated failed"
        raise RuntimeError(detail)

    is_outdated = bool((result.stdout or "").strip())
    return is_outdated, None


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
        return "Run `uv tool upgrade basic-memory`."
    if source == InstallSource.HOMEBREW:
        return "Run `brew upgrade basic-memory`."
    return (
        "Automatic install is not supported for this environment. "
        "Update with your package manager (for pip: `python3 -m pip install -U basic-memory`)."
    )


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
            logger.warning("Auto-update interval gate skipped due incompatible timestamp format")
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
        if source == InstallSource.HOMEBREW:
            update_available, latest_version = _check_homebrew_update_available(silent=silent)
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

        # --- Automatic install ---
        command = (
            ["uv", "tool", "upgrade", PACKAGE_NAME]
            if source == InstallSource.UV_TOOL
            else ["brew", "upgrade", PACKAGE_NAME]
        )
        timeout = (
            UV_UPGRADE_TIMEOUT_SECONDS
            if source == InstallSource.UV_TOOL
            else BREW_UPGRADE_TIMEOUT_SECONDS
        )

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
            out.print(f"[green]{result.message}[/green]")
        elif result.status == AutoUpdateStatus.FAILED:
            error_detail = f" {result.error}" if result.error else ""
            out.print(f"[yellow]{result.message}{error_detail}[/yellow]")
        elif result.message:
            out.print(f"[cyan]{result.message}[/cyan]")

    return result
