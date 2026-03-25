"""Tests for optional Logfire bootstrap helpers."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import copy_context

from loguru import logger
from basic_memory import telemetry
from basic_memory.config import init_api_logging, init_cli_logging, init_mcp_logging


class FakeLogfire:
    """Small fake Logfire surface for bootstrap testing."""

    class CodeSource:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    def __init__(self, *, fail_on_send_to_logfire: bool = False) -> None:
        self.fail_on_send_to_logfire = fail_on_send_to_logfire
        self.configure_calls: list[dict] = []
        self.span_calls: list[tuple[str, dict]] = []

    def configure(self, **kwargs) -> None:
        self.configure_calls.append(kwargs)
        if self.fail_on_send_to_logfire and "send_to_logfire" in kwargs:
            raise TypeError("send_to_logfire not supported")

    def loguru_handler(self) -> dict:
        return {"sink": "fake-logfire", "level": "INFO"}

    @contextmanager
    def span(self, name: str, **attrs):
        self.span_calls.append((name, attrs))
        yield


def test_configure_telemetry_disabled_is_noop() -> None:
    telemetry.reset_telemetry_state()

    enabled = telemetry.configure_telemetry(
        "basic-memory-cli",
        environment="dev",
        enable_logfire=False,
    )

    assert enabled is False
    assert telemetry.telemetry_enabled() is False
    assert telemetry.get_logfire_handler() is None
    assert telemetry.pop_telemetry_warnings() == []


def test_configure_telemetry_warns_when_dependency_missing(monkeypatch) -> None:
    telemetry.reset_telemetry_state()
    monkeypatch.setattr(telemetry, "_load_logfire", lambda: None)

    enabled = telemetry.configure_telemetry(
        "basic-memory-cli",
        environment="dev",
        enable_logfire=True,
    )

    assert enabled is False
    assert telemetry.telemetry_enabled() is False
    assert telemetry.get_logfire_handler() is None
    assert telemetry.pop_telemetry_warnings() == [
        "Logfire telemetry was enabled but the 'logfire' package is not installed. "
        "Telemetry remains disabled."
    ]


def test_configure_telemetry_retries_without_send_to_logfire(monkeypatch) -> None:
    fake_logfire = FakeLogfire(fail_on_send_to_logfire=True)
    telemetry.reset_telemetry_state()
    monkeypatch.setattr(telemetry, "_load_logfire", lambda: fake_logfire)

    enabled = telemetry.configure_telemetry(
        "basic-memory-api",
        environment="prod",
        service_version="abc123",
        enable_logfire=True,
        send_to_logfire=True,
    )

    assert enabled is True
    assert telemetry.telemetry_enabled() is True
    assert telemetry.get_logfire_handler() == {"sink": "fake-logfire", "level": "INFO"}
    assert len(fake_logfire.configure_calls) == 2
    assert fake_logfire.configure_calls[0]["send_to_logfire"] is True
    assert "send_to_logfire" not in fake_logfire.configure_calls[1]


def test_bind_telemetry_context_filters_nulls() -> None:
    bound = telemetry.bind_telemetry_context(project_name="main", workspace_id=None)
    extra = bound._options[-1]  # type: ignore[attr-defined]
    assert extra == {"project_name": "main"}


def test_bind_telemetry_context_merges_active_context() -> None:
    with telemetry.contextualize(project_name="main", route_mode="local_asgi"):
        bound = telemetry.bind_telemetry_context(tool_name="write_note", workspace_id=None)

    extra = bound._options[-1]  # type: ignore[attr-defined]
    assert extra == {
        "project_name": "main",
        "route_mode": "local_asgi",
        "tool_name": "write_note",
    }


def test_contextualize_adds_filtered_loguru_context() -> None:
    records: list[dict] = []
    sink_id = logger.add(lambda message: records.append(message.record["extra"].copy()))

    try:
        with telemetry.contextualize(project_name="main", workspace_id=None):
            logger.info("inside telemetry context")
    finally:
        logger.remove(sink_id)

    assert records == [{"project_name": "main"}]


def test_contextualize_nested_contexts_merge_and_unwind() -> None:
    records: list[dict] = []
    sink_id = logger.add(lambda message: records.append(message.record["extra"].copy()))

    try:
        with telemetry.contextualize(project_name="main"):
            logger.info("outer context")
            with telemetry.contextualize(tool_name="write_note"):
                logger.info("inner context")
            logger.info("outer context restored")
    finally:
        logger.remove(sink_id)

    assert records == [
        {"project_name": "main"},
        {"project_name": "main", "tool_name": "write_note"},
        {"project_name": "main"},
    ]


def test_span_uses_logfire_when_enabled(monkeypatch) -> None:
    fake_logfire = FakeLogfire()
    telemetry.reset_telemetry_state()
    monkeypatch.setattr(telemetry, "_load_logfire", lambda: fake_logfire)
    telemetry.configure_telemetry(
        "basic-memory-mcp",
        environment="dev",
        enable_logfire=True,
    )

    with telemetry.span("mcp.tool.write_note", project_name="main", workspace_id=None):
        pass

    assert fake_logfire.span_calls == [("mcp.tool.write_note", {"project_name": "main"})]


def test_operation_creates_span_and_log_context(monkeypatch) -> None:
    fake_logfire = FakeLogfire()
    records: list[dict] = []
    sink_id = logger.add(lambda message: records.append(message.record["extra"].copy()))

    telemetry.reset_telemetry_state()
    monkeypatch.setattr(telemetry, "_load_logfire", lambda: fake_logfire)
    telemetry.configure_telemetry(
        "basic-memory-cli",
        environment="dev",
        enable_logfire=True,
    )

    try:
        with telemetry.operation(
            "cli.command.status",
            entrypoint="cli",
            command_name="status",
            workspace_id=None,
        ):
            logger.info("inside operation")
    finally:
        logger.remove(sink_id)

    assert fake_logfire.span_calls == [
        ("cli.command.status", {"entrypoint": "cli", "command_name": "status"})
    ]
    assert records == [{"entrypoint": "cli", "command_name": "status"}]


def test_scope_creates_span_and_nested_log_context(monkeypatch) -> None:
    fake_logfire = FakeLogfire()
    records: list[dict] = []
    sink_id = logger.add(lambda message: records.append(message.record["extra"].copy()))

    telemetry.reset_telemetry_state()
    monkeypatch.setattr(telemetry, "_load_logfire", lambda: fake_logfire)
    telemetry.configure_telemetry(
        "basic-memory-mcp",
        environment="dev",
        enable_logfire=True,
    )

    try:
        with telemetry.contextualize(project_name="main"):
            with telemetry.scope(
                "routing.client_session",
                route_mode="local_asgi",
                workspace_id=None,
            ):
                logger.info("inside scope")
    finally:
        logger.remove(sink_id)

    assert fake_logfire.span_calls == [
        ("routing.client_session", {"route_mode": "local_asgi"})
    ]
    assert records == [{"project_name": "main", "route_mode": "local_asgi"}]


def test_contextualize_isolated_per_context() -> None:
    def read_extra() -> dict:
        holder: list[dict] = []
        sink_id = logger.add(lambda message: holder.append(message.record["extra"].copy()))
        try:
            logger.info("outside context")
        finally:
            logger.remove(sink_id)
        return holder[0]

    with telemetry.contextualize(project_name="main"):
        inside = read_extra()

    outside = copy_context().run(read_extra)

    assert inside == {"project_name": "main"}
    assert outside == {}


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
            "service_version": "0.20.2",
            "enable_logfire": True,
            "send_to_logfire": False,
        },
        {
            "service_name": "basic-memory-mcp",
            "environment": "staging",
            "service_version": "0.20.2",
            "enable_logfire": True,
            "send_to_logfire": False,
        },
        {
            "service_name": "basic-memory-api",
            "environment": "staging",
            "service_version": "0.20.2",
            "enable_logfire": True,
            "send_to_logfire": False,
        },
    ]
    assert setup_calls == [
        {"log_level": "DEBUG", "log_to_file": True},
        {"log_level": "DEBUG", "log_to_file": True},
        {"log_level": "DEBUG", "log_to_file": True},
    ]
