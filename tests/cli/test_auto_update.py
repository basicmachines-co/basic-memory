"""Tests for CLI auto-update behavior."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from io import StringIO

from rich.console import Console

from basic_memory.cli.auto_update import (
    AutoUpdateResult,
    AutoUpdateStatus,
    InstallSource,
    _is_interactive_session,
    detect_install_source,
    maybe_run_periodic_auto_update,
    run_auto_update,
)
from basic_memory.config import BasicMemoryConfig


class StubConfigManager:
    """Simple in-memory ConfigManager stub for updater tests."""

    def __init__(self, config: BasicMemoryConfig):
        self._config = config
        self.save_calls = 0

    def load_config(self) -> BasicMemoryConfig:
        return self._config

    def save_config(self, config: BasicMemoryConfig) -> None:
        self._config = config
        self.save_calls += 1


def _capture_console() -> tuple[Console, StringIO]:
    """Create a Console that writes to an in-memory buffer."""
    buf = StringIO()
    return Console(file=buf, force_terminal=True), buf


def _base_config(tmp_path) -> BasicMemoryConfig:
    return BasicMemoryConfig(projects={"main": {"path": str(tmp_path / "main")}})


def _result(
    status: AutoUpdateStatus,
    *,
    message: str | None,
    error: str | None = None,
) -> AutoUpdateResult:
    return AutoUpdateResult(
        status=status,
        source=InstallSource.UV_TOOL,
        checked=True,
        update_available=status in {AutoUpdateStatus.UPDATE_AVAILABLE, AutoUpdateStatus.UPDATED},
        updated=status == AutoUpdateStatus.UPDATED,
        latest_version="9.9.9",
        message=message,
        error=error,
        restart_recommended=status == AutoUpdateStatus.UPDATED,
    )


def test_detect_install_source_variants():
    assert (
        detect_install_source("/opt/homebrew/Cellar/basic-memory/0.18.0/bin/python")
        == InstallSource.HOMEBREW
    )
    assert (
        detect_install_source("/Users/me/.local/share/uv/tools/basic-memory/bin/python")
        == InstallSource.UV_TOOL
    )
    assert (
        detect_install_source("/Users/me/.cache/uv/archive-v0/abc123/bin/python")
        == InstallSource.UVX
    )
    assert (
        detect_install_source("/Users/me/Library/Caches/uv/archive-v0/abc123/bin/python")
        == InstallSource.UVX
    )
    assert detect_install_source("/usr/local/bin/python3") == InstallSource.UNKNOWN


def test_interval_gate_skips_check_when_recent(tmp_path):
    config = _base_config(tmp_path)
    config.auto_update_last_checked_at = datetime.now() - timedelta(seconds=30)
    config.update_check_interval = 3600
    manager = StubConfigManager(config)

    result = run_auto_update(config_manager=manager)

    assert result.status == AutoUpdateStatus.SKIPPED
    assert result.checked is False
    assert manager.save_calls == 0


def test_auto_update_disabled_skips_periodic(tmp_path):
    config = _base_config(tmp_path)
    config.auto_update = False
    manager = StubConfigManager(config)

    result = run_auto_update(config_manager=manager)

    assert result.status == AutoUpdateStatus.SKIPPED
    assert result.checked is False


def test_force_bypasses_auto_update_disabled(monkeypatch, tmp_path):
    config = _base_config(tmp_path)
    config.auto_update = False
    manager = StubConfigManager(config)

    monkeypatch.setattr(
        "basic_memory.cli.auto_update._check_pypi_update_available",
        lambda: (False, "0.0.0"),
    )

    result = run_auto_update(
        force=True,
        config_manager=manager,
        executable="/Users/me/.local/share/uv/tools/basic-memory/bin/python",
    )

    assert result.status == AutoUpdateStatus.UP_TO_DATE
    assert result.checked is True
    assert manager.save_calls == 1


def test_homebrew_outdated_triggers_upgrade(monkeypatch, tmp_path):
    config = _base_config(tmp_path)
    manager = StubConfigManager(config)

    monkeypatch.setattr(
        "basic_memory.cli.auto_update._check_homebrew_update_available",
        lambda silent: (True, None),
    )
    calls: list[list[str]] = []

    def _fake_run_subprocess(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("basic_memory.cli.auto_update._run_subprocess", _fake_run_subprocess)

    result = run_auto_update(
        config_manager=manager,
        executable="/opt/homebrew/Cellar/basic-memory/0.18.0/bin/python",
    )

    assert result.status == AutoUpdateStatus.UPDATED
    assert calls == [["brew", "upgrade", "basic-memory"]]


def test_uv_tool_pypi_check_triggers_upgrade(monkeypatch, tmp_path):
    config = _base_config(tmp_path)
    manager = StubConfigManager(config)

    monkeypatch.setattr(
        "basic_memory.cli.auto_update._check_pypi_update_available",
        lambda: (True, "9.9.9"),
    )
    calls: list[list[str]] = []

    def _fake_run_subprocess(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("basic_memory.cli.auto_update._run_subprocess", _fake_run_subprocess)

    result = run_auto_update(
        config_manager=manager,
        executable="/Users/me/.local/share/uv/tools/basic-memory/bin/python",
    )

    assert result.status == AutoUpdateStatus.UPDATED
    assert result.latest_version == "9.9.9"
    assert calls == [["uv", "tool", "upgrade", "basic-memory"]]


def test_unknown_manager_returns_manual_update_guidance(monkeypatch, tmp_path):
    config = _base_config(tmp_path)
    manager = StubConfigManager(config)
    monkeypatch.setattr(
        "basic_memory.cli.auto_update._check_pypi_update_available",
        lambda: (True, "9.9.9"),
    )

    result = run_auto_update(
        force=True,
        config_manager=manager,
        executable="/usr/local/bin/python3",
    )

    assert result.status == AutoUpdateStatus.UPDATE_AVAILABLE
    assert result.updated is False
    assert "Automatic install is not supported" in (result.message or "")


def test_uvx_runtime_is_skipped(monkeypatch, tmp_path):
    config = _base_config(tmp_path)
    manager = StubConfigManager(config)

    result = run_auto_update(
        config_manager=manager,
        executable="/Users/me/.cache/uv/archive-v0/abc123/bin/python",
    )

    assert result.status == AutoUpdateStatus.SKIPPED
    assert result.source == InstallSource.UVX
    assert result.checked is False
    assert manager.save_calls == 0


def test_mcp_silent_mode_suppresses_subprocess_output(monkeypatch, tmp_path):
    config = _base_config(tmp_path)
    manager = StubConfigManager(config)
    monkeypatch.setattr(
        "basic_memory.cli.auto_update._check_pypi_update_available",
        lambda: (True, "9.9.9"),
    )

    captured_kwargs: list[dict] = []

    def _fake_run_subprocess(command, **kwargs):
        captured_kwargs.append(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("basic_memory.cli.auto_update._run_subprocess", _fake_run_subprocess)

    result = run_auto_update(
        config_manager=manager,
        executable="/Users/me/.local/share/uv/tools/basic-memory/bin/python",
        silent=True,
    )

    assert result.status == AutoUpdateStatus.UPDATED
    assert captured_kwargs
    assert captured_kwargs[0]["silent"] is True
    assert captured_kwargs[0]["capture_output"] is False


def test_subprocess_oserror_is_non_fatal(monkeypatch, tmp_path):
    config = _base_config(tmp_path)
    manager = StubConfigManager(config)
    monkeypatch.setattr(
        "basic_memory.cli.auto_update._check_pypi_update_available",
        lambda: (True, "9.9.9"),
    )

    def _raise_oserror(command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr("basic_memory.cli.auto_update._run_subprocess", _raise_oserror)

    result = run_auto_update(
        config_manager=manager,
        executable="/Users/me/.local/share/uv/tools/basic-memory/bin/python",
    )

    assert result.status == AutoUpdateStatus.FAILED
    assert result.checked is True


def test_mixed_timezone_timestamp_does_not_crash_interval_gate(monkeypatch, tmp_path):
    config = _base_config(tmp_path)
    config.auto_update_last_checked_at = datetime.now(timezone.utc)
    manager = StubConfigManager(config)

    monkeypatch.setattr(
        "basic_memory.cli.auto_update._check_pypi_update_available",
        lambda: (False, "0.0.0"),
    )

    result = run_auto_update(
        config_manager=manager,
        executable="/Users/me/.local/share/uv/tools/basic-memory/bin/python",
    )

    assert result.status == AutoUpdateStatus.UP_TO_DATE
    assert result.checked is True


def test_maybe_run_periodic_auto_update_non_interactive_has_no_console_output():
    console, buf = _capture_console()
    result = maybe_run_periodic_auto_update(
        "status",
        is_interactive=False,
        console=console,
    )
    assert result is None
    assert buf.getvalue() == ""


def test_maybe_run_periodic_auto_update_prints_updated(monkeypatch):
    console, buf = _capture_console()
    monkeypatch.setattr(
        "basic_memory.cli.auto_update.run_auto_update",
        lambda **kwargs: _result(
            AutoUpdateStatus.UPDATED,
            message="Basic Memory was updated successfully.",
        ),
    )

    result = maybe_run_periodic_auto_update("status", is_interactive=True, console=console)
    assert result is not None
    assert result.status == AutoUpdateStatus.UPDATED
    assert "updated successfully" in buf.getvalue().lower()


def test_maybe_run_periodic_auto_update_prints_available(monkeypatch):
    console, buf = _capture_console()
    monkeypatch.setattr(
        "basic_memory.cli.auto_update.run_auto_update",
        lambda **kwargs: _result(
            AutoUpdateStatus.UPDATE_AVAILABLE,
            message="Update available (latest: 9.9.9).",
        ),
    )

    result = maybe_run_periodic_auto_update("status", is_interactive=True, console=console)
    assert result is not None
    assert result.status == AutoUpdateStatus.UPDATE_AVAILABLE
    assert "update available" in buf.getvalue().lower()


def test_maybe_run_periodic_auto_update_prints_failed_with_error(monkeypatch):
    console, buf = _capture_console()
    monkeypatch.setattr(
        "basic_memory.cli.auto_update.run_auto_update",
        lambda **kwargs: _result(
            AutoUpdateStatus.FAILED,
            message="Automatic update check failed.",
            error="network timeout",
        ),
    )

    result = maybe_run_periodic_auto_update("status", is_interactive=True, console=console)
    assert result is not None
    assert result.status == AutoUpdateStatus.FAILED
    output = buf.getvalue().lower()
    assert "automatic update check failed" in output
    assert "network timeout" in output


def test_maybe_run_periodic_auto_update_uses_interactive_probe_when_not_overridden(monkeypatch):
    console, buf = _capture_console()
    monkeypatch.setattr("basic_memory.cli.auto_update._is_interactive_session", lambda: True)
    monkeypatch.setattr(
        "basic_memory.cli.auto_update.run_auto_update",
        lambda **kwargs: _result(
            AutoUpdateStatus.UP_TO_DATE,
            message="Basic Memory is up to date.",
        ),
    )

    result = maybe_run_periodic_auto_update("status", console=console)
    assert result is not None
    assert result.status == AutoUpdateStatus.UP_TO_DATE
    # UP_TO_DATE is intentionally silent for periodic checks.
    assert buf.getvalue() == ""


def test_is_interactive_session_handles_closed_stdio(monkeypatch):
    class _BrokenStream:
        def isatty(self) -> bool:
            raise ValueError("I/O operation on closed file")

    monkeypatch.setattr("basic_memory.cli.auto_update.sys.stdin", _BrokenStream())
    monkeypatch.setattr("basic_memory.cli.auto_update.sys.stdout", _BrokenStream())

    assert _is_interactive_session() is False
