"""Tests for logging setup helpers."""

from basic_memory import utils


def test_setup_logging_uses_shared_log_file_off_windows(monkeypatch, tmp_path) -> None:
    """Non-Windows platforms should keep the shared log filename."""
    added_sinks: list[str] = []

    monkeypatch.setenv("BASIC_MEMORY_ENV", "dev")
    monkeypatch.setattr(utils.os, "name", "posix")
    monkeypatch.setattr(utils.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(utils.logger, "remove", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        utils.logger,
        "add",
        lambda sink, **kwargs: added_sinks.append(str(sink)),
    )

    utils.setup_logging(log_to_file=True)

    assert added_sinks == [str(tmp_path / ".basic-memory" / "basic-memory.log")]


def test_setup_logging_uses_per_process_log_file_on_windows(monkeypatch, tmp_path) -> None:
    """Windows uses per-process logs so rotation never contends across processes."""
    added_sinks: list[str] = []

    monkeypatch.setenv("BASIC_MEMORY_ENV", "dev")
    monkeypatch.setattr(utils.os, "name", "nt")
    monkeypatch.setattr(utils.os, "getpid", lambda: 4242)
    monkeypatch.setattr(utils.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(utils.logger, "remove", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        utils.logger,
        "add",
        lambda sink, **kwargs: added_sinks.append(str(sink)),
    )

    utils.setup_logging(log_to_file=True)

    assert added_sinks == [str(tmp_path / ".basic-memory" / "basic-memory-4242.log")]
