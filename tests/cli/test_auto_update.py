"""Tests for CLI auto-update behavior."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from io import StringIO
from typing import Any, cast

import pytest
from rich.console import Console

from basic_memory.cli.auto_update import (
    AutoUpdateResult,
    AutoUpdateStatus,
    HomebrewCheckError,
    InstallSource,
    _check_homebrew_update_available,
    _is_interactive_session,
    _preload_lazy_console_modules,
    print_update_status,
    detect_install_source,
    maybe_run_periodic_auto_update,
    run_auto_update,
)
from basic_memory.config import BasicMemoryConfig

UNTRUSTED_TAP_STDERR = (
    "Error: Refusing to load formula basicmachines-co/basic-memory/basic-memory "
    "from untrusted tap basicmachines-co/basic-memory."
)


def _brew_outdated_payload(current_version: str | None = None) -> str:
    formulae = []
    if current_version is not None:
        formulae.append(
            {
                "name": "basicmachines-co/basic-memory/basic-memory",
                "installed_versions": ["0.22.1"],
                "current_version": current_version,
                "pinned": False,
                "pinned_version": None,
            }
        )
    return json.dumps({"formulae": formulae, "casks": []})


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


def _config_manager(manager: StubConfigManager) -> Any:
    return cast(Any, manager)


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

    result = run_auto_update(config_manager=_config_manager(manager))

    assert result.status == AutoUpdateStatus.SKIPPED
    assert result.checked is False
    assert manager.save_calls == 0


def test_auto_update_disabled_skips_periodic(tmp_path):
    config = _base_config(tmp_path)
    config.auto_update = False
    manager = StubConfigManager(config)

    result = run_auto_update(config_manager=_config_manager(manager))

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
        config_manager=_config_manager(manager),
        executable="/Users/me/.local/share/uv/tools/basic-memory/bin/python",
    )

    assert result.status == AutoUpdateStatus.UP_TO_DATE
    assert result.checked is True
    assert manager.save_calls == 1


def test_check_homebrew_update_available_exit_code_1_means_outdated(monkeypatch):
    """brew outdated exits 1 when the formula is outdated, not on error.

    Exit 1 is shared with the failure case, so the JSON formula entry is the
    discriminator. brew may also write progress chatter to stderr on this path,
    so a non-empty stderr must not be read as an error.
    """

    def _fake_run(command, **kwargs):
        assert command == ["brew", "outdated", "--json=v2", "basic-memory"]
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=_brew_outdated_payload("0.23.0"),
            stderr="==> Downloading Homebrew API data",
        )

    monkeypatch.setattr("basic_memory.cli.auto_update._run_subprocess", _fake_run)
    is_outdated, latest_version = _check_homebrew_update_available(silent=False)
    assert is_outdated is True
    assert latest_version == "0.23.0"


def test_check_homebrew_update_available_exit_code_0_means_up_to_date(monkeypatch):
    """brew outdated exits 0 when the formula is up to date."""

    def _fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_brew_outdated_payload(),
            stderr="",
        )

    monkeypatch.setattr("basic_memory.cli.auto_update._run_subprocess", _fake_run)
    is_outdated, _ = _check_homebrew_update_available(silent=False)
    assert is_outdated is False


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "omitted the formulae list"),
        ({"formulae": [None]}, "malformed formula"),
        (
            {"formulae": [{"name": "basicmachines-co/basic-memory/basic-memory"}]},
            "omitted the formula current_version",
        ),
        (
            {"formulae": [{"name": "another-formula", "current_version": "1.0.0"}]},
            "exited 1",
        ),
    ],
)
def test_check_homebrew_update_available_rejects_malformed_json(
    monkeypatch,
    payload,
    message,
):
    """A syntactically valid but incomplete response is still not an answer."""

    def _fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("basic_memory.cli.auto_update._run_subprocess", _fake_run)

    with pytest.raises(HomebrewCheckError, match=message):
        _check_homebrew_update_available(silent=False)


def test_check_homebrew_update_available_failed_check_is_not_up_to_date(monkeypatch):
    """A failed `brew outdated` exits non-zero with empty stdout -- it is not an answer."""

    def _fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=UNTRUSTED_TAP_STDERR)

    monkeypatch.setattr("basic_memory.cli.auto_update._run_subprocess", _fake_run)

    with pytest.raises(HomebrewCheckError) as excinfo:
        _check_homebrew_update_available(silent=False)

    assert "untrusted tap" in str(excinfo.value)


def test_check_homebrew_update_available_reports_missing_brew(monkeypatch):
    """brew not on PATH must surface as an unanswered check, not as up to date."""

    def _raise_not_found(command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr("basic_memory.cli.auto_update._run_subprocess", _raise_not_found)

    with pytest.raises(HomebrewCheckError):
        _check_homebrew_update_available(silent=False)


def test_failed_homebrew_check_falls_back_to_pypi(monkeypatch, tmp_path):
    """Regression: a failed brew check reported the install as up to date.

    The untrusted tap is one trigger; a stale tap, a missing formula, or a network
    failure produce the same empty-stdout shape.
    """
    config = _base_config(tmp_path)
    manager = StubConfigManager(config)

    def _fake_run(command, **kwargs):
        assert command[:2] == ["brew", "outdated"]
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=UNTRUSTED_TAP_STDERR)

    monkeypatch.setattr("basic_memory.cli.auto_update._run_subprocess", _fake_run)
    monkeypatch.setattr(
        "basic_memory.cli.auto_update._check_pypi_update_available",
        lambda: (True, "9.9.9"),
    )

    result = run_auto_update(
        check_only=True,
        config_manager=_config_manager(manager),
        executable="/opt/homebrew/Cellar/basic-memory/0.18.0/bin/python",
    )

    assert result.status == AutoUpdateStatus.UPDATE_AVAILABLE
    assert result.latest_version == "9.9.9"
    assert "brew upgrade basic-memory" in (result.message or "")


def test_failed_homebrew_check_does_not_auto_upgrade(monkeypatch, tmp_path):
    """Availability inferred from PyPI must not trigger an automatic `brew upgrade`.

    release.yml publishes to PyPI in the `release` job and the Homebrew formula job
    `needs: release`, so PyPI can carry a version the tap cannot install yet. On top
    of that, whatever hid the brew answer (untrusted tap, missing brew) also blocks
    the upgrade -- so acting on the PyPI answer runs a doomed command.
    """
    config = _base_config(tmp_path)
    manager = StubConfigManager(config)
    calls: list[list[str]] = []

    def _fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=UNTRUSTED_TAP_STDERR)

    monkeypatch.setattr("basic_memory.cli.auto_update._run_subprocess", _fake_run)
    monkeypatch.setattr(
        "basic_memory.cli.auto_update._check_pypi_update_available",
        lambda: (True, "9.9.9"),
    )

    result = run_auto_update(
        config_manager=_config_manager(manager),
        executable="/opt/homebrew/Cellar/basic-memory/0.18.0/bin/python",
    )

    assert result.status == AutoUpdateStatus.UPDATE_AVAILABLE
    assert result.updated is False
    assert result.latest_version == "9.9.9"
    assert ["brew", "upgrade", "basic-memory"] not in calls
    assert "untrusted tap" in (result.message or "")
    assert "brew upgrade basic-memory" in (result.message or "")


def test_failed_homebrew_check_still_trusts_a_pypi_negative(monkeypatch, tmp_path):
    """The fallback stays authoritative for "nothing newer exists".

    The tap can only lag PyPI, never lead it, so a PyPI "up to date" is sound even
    when brew could not answer. Only the positive answer is unsafe to act on.
    """
    config = _base_config(tmp_path)
    manager = StubConfigManager(config)

    def _fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=UNTRUSTED_TAP_STDERR)

    monkeypatch.setattr("basic_memory.cli.auto_update._run_subprocess", _fake_run)
    monkeypatch.setattr(
        "basic_memory.cli.auto_update._check_pypi_update_available",
        lambda: (False, "0.0.0"),
    )

    result = run_auto_update(
        config_manager=_config_manager(manager),
        executable="/opt/homebrew/Cellar/basic-memory/0.18.0/bin/python",
    )

    assert result.status == AutoUpdateStatus.UP_TO_DATE
    assert result.update_available is False


def test_failed_homebrew_check_reports_failure_when_pypi_is_unreachable(monkeypatch, tmp_path):
    """With neither source able to answer, report the failure rather than success."""
    config = _base_config(tmp_path)
    manager = StubConfigManager(config)

    def _fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=UNTRUSTED_TAP_STDERR)

    def _pypi_unreachable():
        raise urllib.error.URLError("network unreachable")

    monkeypatch.setattr("basic_memory.cli.auto_update._run_subprocess", _fake_run)
    monkeypatch.setattr(
        "basic_memory.cli.auto_update._check_pypi_update_available", _pypi_unreachable
    )

    result = run_auto_update(
        config_manager=_config_manager(manager),
        executable="/opt/homebrew/Cellar/basic-memory/0.18.0/bin/python",
    )

    assert result.status == AutoUpdateStatus.FAILED
    assert result.update_available is False


def test_preload_lazy_console_modules_imports_deferred_modules(monkeypatch):
    # Regression: the in-place upgrade deletes the running install's files, so
    # any module rich/typer defers until print time must already be loaded or
    # the final status message crashes with ModuleNotFoundError.
    monkeypatch.delitem(sys.modules, "rich._emoji_codes", raising=False)
    monkeypatch.delitem(sys.modules, "typer.rich_utils", raising=False)

    _preload_lazy_console_modules()

    assert "rich._emoji_codes" in sys.modules
    assert "typer.rich_utils" in sys.modules


class _UpgradedAwayFinder:
    """Stand-in for the deleted install prefix: nothing new can be imported."""

    def find_spec(self, fullname, path=None, target=None):  # noqa: D102
        raise ModuleNotFoundError(f"No module named {fullname!r}")


def _cool_deferred_width_table(monkeypatch) -> None:
    """Return rich to the cold state a freshly started process is in.

    rich caches the Unicode cell-width table aggressively, and earlier tests in
    this session will already have warmed it -- without this the regression test
    below passes whether or not the table was preloaded.
    """
    import rich.cells

    for cached in ("cached_cell_len", "get_character_cell_size"):
        clear = getattr(getattr(rich.cells, cached, None), "cache_clear", None)
        if clear is not None:
            clear()

    # rich >= 14.2 splits the tables into rich._unicode_data.unicode<version>;
    # older versions inline them and have nothing to unload.
    unicode_data = sys.modules.get("rich._unicode_data")
    clear_load = getattr(getattr(unicode_data, "load", None), "cache_clear", None)
    if clear_load is not None:
        clear_load()
    for name in [n for n in sys.modules if n.startswith("rich._unicode_data.unicode")]:
        monkeypatch.delitem(sys.modules, name, raising=False)


def test_status_message_survives_upgraded_away_install(monkeypatch):
    # Regression (#1316): `brew upgrade` removes the running install's files, so
    # the status message printed afterwards must not need any new import. The
    # message is long and non-ASCII on purpose -- that is what makes rich wrap
    # the line and reach for the deferred cell-width table.
    output = StringIO()
    console = Console(width=40, file=output)
    console.print("warm up the print path")
    _cool_deferred_width_table(monkeypatch)

    _preload_lazy_console_modules()
    monkeypatch.setattr(sys, "meta_path", [_UpgradedAwayFinder(), *sys.meta_path])

    # Deliberately not print_update_status: its fallback would mask the bug.
    console.print(
        "[red]Automatic update failed. Error: could not link \u2018basic-memory\u2019 "
        "\u2014 the files were replaced while running. " + "detail " * 12 + "[/red]"
    )

    # rich wrapped and styled the line; normalize before checking the content.
    rendered = re.sub(r"\x1b\[[0-9;]*m", "", output.getvalue())
    assert "could not link" in " ".join(rendered.split())


def test_print_update_status_falls_back_to_plain_output(capsys):
    # A status line must never be what fails a command whose upgrade succeeded.
    class ExplodingConsole:
        def print(self, *args, **kwargs):
            raise ModuleNotFoundError("No module named 'rich._unicode_data.unicode17-0-0'")

    print_update_status(cast(Console, ExplodingConsole()), "Basic Memory was updated.", "green")

    assert "Basic Memory was updated." in capsys.readouterr().out


def test_homebrew_outdated_triggers_upgrade(monkeypatch, tmp_path):
    config = _base_config(tmp_path)
    manager = StubConfigManager(config)

    monkeypatch.setattr(
        "basic_memory.cli.auto_update._check_homebrew_update_available",
        lambda silent: (True, "0.23.0"),
    )
    calls: list[list[str]] = []

    def _fake_run_subprocess(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("basic_memory.cli.auto_update._run_subprocess", _fake_run_subprocess)

    result = run_auto_update(
        config_manager=_config_manager(manager),
        executable="/opt/homebrew/Cellar/basic-memory/0.18.0/bin/python",
    )

    assert result.status == AutoUpdateStatus.UPDATED
    assert result.latest_version == "0.23.0"
    assert calls == [["brew", "upgrade", "basic-memory"]]


def test_homebrew_outdated_check_only_reports_latest_version(monkeypatch, tmp_path):
    config = _base_config(tmp_path)
    manager = StubConfigManager(config)

    monkeypatch.setattr(
        "basic_memory.cli.auto_update._check_homebrew_update_available",
        lambda silent: (True, "0.23.0"),
    )

    result = run_auto_update(
        check_only=True,
        config_manager=_config_manager(manager),
        executable="/opt/homebrew/Cellar/basic-memory/0.22.1/bin/python",
    )

    assert result.status == AutoUpdateStatus.UPDATE_AVAILABLE
    assert result.latest_version == "0.23.0"
    assert "latest: 0.23.0" in (result.message or "")
    assert "unknown" not in (result.message or "")


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
        config_manager=_config_manager(manager),
        executable="/Users/me/.local/share/uv/tools/basic-memory/bin/python",
    )

    assert result.status == AutoUpdateStatus.UPDATED
    assert result.latest_version == "9.9.9"
    assert calls == [["uv", "tool", "upgrade", "basic-memory", "--prerelease=allow"]]


def test_unknown_manager_returns_manual_update_guidance(monkeypatch, tmp_path):
    config = _base_config(tmp_path)
    manager = StubConfigManager(config)
    monkeypatch.setattr(
        "basic_memory.cli.auto_update._check_pypi_update_available",
        lambda: (True, "9.9.9"),
    )

    result = run_auto_update(
        force=True,
        config_manager=_config_manager(manager),
        executable="/usr/local/bin/python3",
    )

    assert result.status == AutoUpdateStatus.UPDATE_AVAILABLE
    assert result.updated is False
    assert "Automatic install is not supported" in (result.message or "")


def test_uvx_runtime_is_skipped(monkeypatch, tmp_path):
    config = _base_config(tmp_path)
    manager = StubConfigManager(config)

    result = run_auto_update(
        config_manager=_config_manager(manager),
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

    captured_kwargs: list[dict[str, Any]] = []

    def _fake_run_subprocess(command, **kwargs):
        captured_kwargs.append(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("basic_memory.cli.auto_update._run_subprocess", _fake_run_subprocess)

    result = run_auto_update(
        config_manager=_config_manager(manager),
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
        config_manager=_config_manager(manager),
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
        config_manager=_config_manager(manager),
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
