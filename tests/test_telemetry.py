"""Tests for Logfire telemetry bootstrap."""

from __future__ import annotations

from basic_memory import __version__, telemetry
from basic_memory.config import init_api_logging, init_cli_logging, init_mcp_logging


class FakeLogfire:
    """Minimal fake of the logfire module surface configure_telemetry touches."""

    class CodeSource:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    def __init__(self, *, fail_on_send_to_logfire: bool = False) -> None:
        self.fail_on_send_to_logfire = fail_on_send_to_logfire
        self.configure_calls: list[dict] = []

    def configure(self, **kwargs) -> None:
        self.configure_calls.append(kwargs)
        if self.fail_on_send_to_logfire and "send_to_logfire" in kwargs:
            raise TypeError("send_to_logfire not supported")

    def loguru_handler(self) -> dict:
        return {"sink": "fake-logfire", "level": "INFO"}


def test_configure_telemetry_disabled_is_noop() -> None:
    enabled = telemetry.configure_telemetry(
        "basic-memory-cli",
        environment="dev",
        enable_logfire=False,
    )

    assert enabled is False
    assert telemetry.get_logfire_handler() is None


def test_configure_telemetry_configures_logfire(monkeypatch) -> None:
    fake_logfire = FakeLogfire()
    monkeypatch.setattr(telemetry, "logfire", fake_logfire)

    enabled = telemetry.configure_telemetry(
        "basic-memory-api",
        environment="prod",
        service_version="abc123",
        enable_logfire=True,
        send_to_logfire=True,
    )

    assert enabled is True
    assert telemetry.get_logfire_handler() == {"sink": "fake-logfire", "level": "INFO"}
    assert len(fake_logfire.configure_calls) == 1
    assert fake_logfire.configure_calls[0]["send_to_logfire"] is True


def test_configure_telemetry_retries_without_send_to_logfire(monkeypatch) -> None:
    fake_logfire = FakeLogfire(fail_on_send_to_logfire=True)
    monkeypatch.setattr(telemetry, "logfire", fake_logfire)

    enabled = telemetry.configure_telemetry(
        "basic-memory-api",
        environment="prod",
        enable_logfire=True,
        send_to_logfire=True,
    )

    assert enabled is True
    assert len(fake_logfire.configure_calls) == 2
    assert fake_logfire.configure_calls[0]["send_to_logfire"] is True
    assert "send_to_logfire" not in fake_logfire.configure_calls[1]


def test_configure_telemetry_clears_handler_when_disabled(monkeypatch) -> None:
    """A prior enabled configure must not leak its handler into a later disabled one."""
    fake_logfire = FakeLogfire()
    monkeypatch.setattr(telemetry, "logfire", fake_logfire)

    telemetry.configure_telemetry("basic-memory-cli", environment="dev", enable_logfire=True)
    assert telemetry.get_logfire_handler() is not None

    telemetry.configure_telemetry("basic-memory-cli", environment="dev", enable_logfire=False)
    assert telemetry.get_logfire_handler() is None


def test_init_logging_functions_configure_telemetry_and_logging(monkeypatch) -> None:
    telemetry_calls: list[dict] = []
    setup_calls: list[dict] = []

    class StubConfig:
        logfire_enabled = True
        logfire_send_to_logfire = False
        logfire_service_name = "basic-memory"
        logfire_environment = "staging"
        env = "dev"

    monkeypatch.setattr(
        "basic_memory.config.ConfigManager", lambda: type("CM", (), {"config": StubConfig()})()
    )
    monkeypatch.setattr(
        "basic_memory.config.configure_telemetry",
        lambda **kwargs: telemetry_calls.append(kwargs),
    )
    monkeypatch.setattr(
        "basic_memory.config.setup_logging",
        lambda **kwargs: setup_calls.append(kwargs),
    )
    monkeypatch.setenv("BASIC_MEMORY_LOG_LEVEL", "DEBUG")
    monkeypatch.delenv("BASIC_MEMORY_CLOUD_MODE", raising=False)

    init_cli_logging()
    init_mcp_logging()
    init_api_logging()

    assert telemetry_calls == [
        {
            "service_name": "basic-memory-cli",
            "environment": "staging",
            "service_version": __version__,
            "enable_logfire": True,
            "send_to_logfire": False,
        },
        {
            "service_name": "basic-memory-mcp",
            "environment": "staging",
            "service_version": __version__,
            "enable_logfire": True,
            "send_to_logfire": False,
        },
        {
            "service_name": "basic-memory-api",
            "environment": "staging",
            "service_version": __version__,
            "enable_logfire": True,
            "send_to_logfire": False,
        },
    ]
    assert setup_calls == [
        {"log_level": "DEBUG", "log_to_file": True},
        {"log_level": "DEBUG", "log_to_file": True},
        {"log_level": "DEBUG", "log_to_file": True},
    ]
