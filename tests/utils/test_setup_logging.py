"""Tests for logging setup helpers."""

import os
import sys
from pathlib import Path

from basic_memory import utils
from typing import Any


def test_setup_logging_uses_shared_log_file_off_windows(monkeypatch, tmp_path) -> None:
    """Non-Windows platforms should keep the shared log filename."""
    added_sinks: list[str] = []

    monkeypatch.setenv("BASIC_MEMORY_ENV", "dev")
    monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
    monkeypatch.setattr(utils.os, "name", "posix")
    monkeypatch.setattr(utils.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(utils.logger, "remove", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        utils.logger,
        "add",
        lambda sink, **kwargs: added_sinks.append(str(sink)),
    )
    monkeypatch.setattr(utils.telemetry, "get_logfire_handler", lambda: None)

    utils.setup_logging(log_to_file=True)

    assert added_sinks == [str(tmp_path / ".basic-memory" / "basic-memory.log")]


def test_setup_logging_uses_per_process_log_file_on_windows(monkeypatch, tmp_path) -> None:
    """Windows uses per-process logs so rotation never contends across processes."""
    added_sinks: list[str] = []

    monkeypatch.setenv("BASIC_MEMORY_ENV", "dev")
    monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
    monkeypatch.setattr(utils.os, "name", "nt")
    monkeypatch.setattr(utils.os, "getpid", lambda: 4242)
    monkeypatch.setattr(utils.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(utils.logger, "remove", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        utils.logger,
        "add",
        lambda sink, **kwargs: added_sinks.append(str(sink)),
    )
    monkeypatch.setattr(utils.telemetry, "get_logfire_handler", lambda: None)

    utils.setup_logging(log_to_file=True)

    assert added_sinks == [str(tmp_path / ".basic-memory" / "basic-memory-4242.log")]


def test_setup_logging_trims_stale_windows_pid_logs(monkeypatch, tmp_path) -> None:
    """Windows cleanup should bound stale PID-specific log files across runs."""
    log_dir = tmp_path / ".basic-memory"
    log_dir.mkdir()

    stale_logs = []
    for index in range(6):
        log_path = log_dir / f"basic-memory-{1000 + index}.log"
        log_path.write_text("old log", encoding="utf-8")
        mtime = 1_000 + index
        os.utime(log_path, (mtime, mtime))
        stale_logs.append(log_path)

    monkeypatch.setenv("BASIC_MEMORY_ENV", "dev")
    monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
    monkeypatch.setattr(utils.os, "name", "nt")
    monkeypatch.setattr(utils.os, "getpid", lambda: 4242)
    monkeypatch.setattr(utils.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(utils.logger, "remove", lambda *args, **kwargs: None)
    monkeypatch.setattr(utils.logger, "add", lambda *args, **kwargs: None)
    monkeypatch.setattr(utils.telemetry, "get_logfire_handler", lambda: None)

    utils.setup_logging(log_to_file=True)

    remaining = sorted(path.name for path in log_dir.glob("basic-memory-*.log*"))
    assert remaining == [
        "basic-memory-1002.log",
        "basic-memory-1003.log",
        "basic-memory-1004.log",
        "basic-memory-1005.log",
    ]


def test_setup_logging_survives_a_stale_log_deleted_mid_cleanup(monkeypatch, tmp_path) -> None:
    """Regression guard for #1211: a stale log that disappears mid-cleanup must not kill the process.

    Windows log files are per-PID, so every launch prunes the same shared directory and concurrent launches
    are the normal case for the Claude Code plugin. The ``unlink`` below is guarded against exactly that race;
    the ``stat`` in the sort key that precedes it was not, so ``FileNotFoundError`` escaped through
    ``setup_logging`` and ended the process during logging setup. When that process was the MCP server, the
    client saw the stdio connection close and the session silently had no basic-memory tools at all.
    """
    log_dir = tmp_path / ".basic-memory"
    log_dir.mkdir()

    for index in range(6):
        log_path = log_dir / f"basic-memory-{1000 + index}.log"
        log_path.write_text("old log", encoding="utf-8")
        mtime = 1_000 + index
        os.utime(log_path, (mtime, mtime))

    vanishing = log_dir / "basic-memory-1003.log"
    real_stat = Path.stat
    race_triggered = False

    def stat_with_a_racing_deletion(self: Path, *args: Any, **kwargs: Any) -> Any:
        nonlocal race_triggered
        # Stand in for another launch pruning the same shared directory between the glob and the stat.
        if self == vanishing and not race_triggered:
            race_triggered = True
            vanishing.unlink()
        return real_stat(self, *args, **kwargs)

    monkeypatch.setenv("BASIC_MEMORY_ENV", "dev")
    monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
    monkeypatch.setattr(utils.os, "name", "nt")
    monkeypatch.setattr(utils.os, "getpid", lambda: 4242)
    monkeypatch.setattr(utils.Path, "home", lambda: tmp_path)
    # Keep file enumeration on the real filesystem so the simulated race occurs in the sort key.
    monkeypatch.setattr(utils.Path, "is_file", lambda path: os.path.isfile(path))
    monkeypatch.setattr(utils.Path, "stat", stat_with_a_racing_deletion)
    monkeypatch.setattr(utils.logger, "remove", lambda *args, **kwargs: None)
    monkeypatch.setattr(utils.logger, "add", lambda *args, **kwargs: None)
    monkeypatch.setattr(utils.telemetry, "get_logfire_handler", lambda: None)

    utils.setup_logging(log_to_file=True)

    # The point is that logging came up at all; the pruning itself is covered by the test above.
    remaining = sorted(path.name for path in log_dir.glob("basic-memory-*.log*"))
    assert race_triggered
    assert "basic-memory-1003.log" not in remaining


def test_setup_logging_honors_basic_memory_config_dir(monkeypatch, tmp_path) -> None:
    """Regression guard for #742: log path must follow BASIC_MEMORY_CONFIG_DIR.

    Prior to #742 the log path was hardcoded to ``~/.basic-memory/``, which
    split state across instances when users set BASIC_MEMORY_CONFIG_DIR to
    isolate config and the database elsewhere.

    Asserts on the log *directory* rather than the exact filename because
    Windows uses a per-process ``basic-memory-<pid>.log`` while POSIX
    shares a single ``basic-memory.log``. The thing this regression guard
    cares about is that the log lives under the redirected config dir,
    not at ``Path.home() / ".basic-memory"``. Patching ``utils.os.name``
    to force one branch would break ``Path(str)`` dispatch on the other
    platform, so we stay platform-agnostic.
    """
    added_sinks: list[str] = []

    custom_dir = tmp_path / "instance-x" / "state"
    monkeypatch.setenv("BASIC_MEMORY_ENV", "dev")
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(custom_dir))
    monkeypatch.setattr(utils.logger, "remove", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        utils.logger,
        "add",
        lambda sink, **kwargs: added_sinks.append(str(sink)),
    )
    monkeypatch.setattr(utils.telemetry, "get_logfire_handler", lambda: None)

    utils.setup_logging(log_to_file=True)

    assert len(added_sinks) == 1
    log_path = Path(added_sinks[0])
    assert log_path.parent == custom_dir
    assert log_path.name.startswith("basic-memory")
    assert log_path.suffix == ".log"


def test_setup_logging_test_env_uses_stderr_only(monkeypatch) -> None:
    """Test mode should add one stderr sink and return before other branches run."""
    added_sinks: list[object] = []
    configured_calls: list[dict[str, Any]] = []

    monkeypatch.setenv("BASIC_MEMORY_ENV", "test")
    monkeypatch.setattr(utils.logger, "remove", lambda *args, **kwargs: None)
    monkeypatch.setattr(utils.logger, "add", lambda sink, **kwargs: added_sinks.append(sink))
    monkeypatch.setattr(
        utils.logger,
        "configure",
        lambda **kwargs: configured_calls.append(kwargs),
    )

    utils.setup_logging(log_to_file=True, log_to_stdout=True, structured_context=True)

    assert added_sinks == [sys.stderr]
    assert configured_calls == []


def test_setup_logging_log_to_stdout(monkeypatch) -> None:
    """stdout logging should attach a stderr sink outside test mode."""
    added_sinks: list[object] = []

    monkeypatch.setenv("BASIC_MEMORY_ENV", "dev")
    monkeypatch.setattr(utils.logger, "remove", lambda *args, **kwargs: None)
    monkeypatch.setattr(utils.logger, "add", lambda sink, **kwargs: added_sinks.append(sink))
    monkeypatch.setattr(utils.telemetry, "get_logfire_handler", lambda: None)

    utils.setup_logging(log_to_stdout=True)

    assert added_sinks == [sys.stderr]


def test_setup_logging_structured_context(monkeypatch) -> None:
    """Structured context should bind cloud metadata into loguru extras."""
    configured_extras: list[dict[str, str]] = []

    monkeypatch.setenv("BASIC_MEMORY_ENV", "dev")
    monkeypatch.setenv("BASIC_MEMORY_TENANT_ID", "tenant-123")
    monkeypatch.setenv("FLY_APP_NAME", "bm-app")
    monkeypatch.setenv("FLY_MACHINE_ID", "machine-123")
    monkeypatch.setenv("FLY_REGION", "ord")
    monkeypatch.setattr(utils.logger, "remove", lambda *args, **kwargs: None)
    monkeypatch.setattr(utils.logger, "add", lambda *args, **kwargs: None)
    monkeypatch.setattr(utils.telemetry, "get_logfire_handler", lambda: None)
    monkeypatch.setattr(
        utils.logger,
        "configure",
        lambda **kwargs: configured_extras.append(kwargs["extra"]),
    )

    utils.setup_logging(structured_context=True)

    assert configured_extras == [
        {
            "tenant_id": "tenant-123",
            "fly_app_name": "bm-app",
            "fly_machine_id": "machine-123",
            "fly_region": "ord",
        }
    ]


def test_setup_logging_suppresses_noisy_loggers(monkeypatch) -> None:
    """Third-party HTTP/file-watch loggers should be raised to WARNING."""
    monkeypatch.setenv("BASIC_MEMORY_ENV", "dev")
    monkeypatch.setattr(utils.logger, "remove", lambda *args, **kwargs: None)
    monkeypatch.setattr(utils.logger, "add", lambda *args, **kwargs: None)
    monkeypatch.setattr(utils.telemetry, "get_logfire_handler", lambda: None)

    httpx_logger = utils.logging.getLogger("httpx")
    watchfiles_logger = utils.logging.getLogger("watchfiles.main")
    original_httpx_level = httpx_logger.level
    original_watchfiles_level = watchfiles_logger.level

    try:
        httpx_logger.setLevel(utils.logging.DEBUG)
        watchfiles_logger.setLevel(utils.logging.INFO)

        utils.setup_logging()

        assert httpx_logger.level == utils.logging.WARNING
        assert watchfiles_logger.level == utils.logging.WARNING
    finally:
        httpx_logger.setLevel(original_httpx_level)
        watchfiles_logger.setLevel(original_watchfiles_level)


def test_setup_logging_adds_logfire_handler(monkeypatch) -> None:
    """Configured Logfire handlers should be added as an extra Loguru sink."""
    added_sinks: list[object] = []

    monkeypatch.setenv("BASIC_MEMORY_ENV", "dev")
    monkeypatch.setattr(utils.logger, "remove", lambda *args, **kwargs: None)
    monkeypatch.setattr(utils.logger, "add", lambda sink, **kwargs: added_sinks.append(sink))
    monkeypatch.setattr(
        utils.telemetry,
        "get_logfire_handler",
        lambda: {"sink": "logfire-sink", "level": "INFO"},
    )

    utils.setup_logging()

    assert added_sinks == ["logfire-sink"]
