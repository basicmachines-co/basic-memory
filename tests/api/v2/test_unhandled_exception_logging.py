"""Logging an unhandled request exception must not repr() the values in its frames.

With loguru's `diagnose=True`, logging a traceback calls repr() on every value named in
every frame. Each ASGI middleware frame holds `scope`, and under FastAPI 0.139
repr(scope) is about 190 MB, so one unhandled exception spent seconds of CPU on the event
loop while every other request waited.
"""

from __future__ import annotations

import os
from typing import override

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from loguru import logger

from basic_memory import utils
from basic_memory.deps.services import get_link_resolver_v2_external


@pytest.fixture
def production_sinks(monkeypatch, tmp_path):
    """The file and stdout sinks a deployed process uses, and nothing else.

    The test run's own sink is set up in test mode, which keeps diagnose=True, so every
    handler is removed for the test and the test-mode setup is restored afterwards.
    """
    monkeypatch.setenv("BASIC_MEMORY_ENV", "dev")
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(utils.telemetry, "get_logfire_handler", lambda: None)
    utils.setup_logging(log_to_file=True, log_to_stdout=True)
    # setup_logging writes a per-process file on Windows, where an open log cannot be
    # renamed for rotation while another process holds it.
    log_name = f"basic-memory-{os.getpid()}.log" if os.name == "nt" else "basic-memory.log"
    try:
        yield tmp_path / log_name
    finally:
        monkeypatch.setenv("BASIC_MEMORY_ENV", "test")
        utils.setup_logging()


class _CountedRepr:
    reprs = 0

    @override
    def __repr__(self) -> str:
        type(self).reprs += 1
        return "<counted>"


def test_a_logged_traceback_does_not_repr_frame_values(production_sinks):
    _CountedRepr.reprs = 0

    def fails(value):
        raise RuntimeError(f"boom {id(value)}")

    def handler():
        held = _CountedRepr()
        try:
            fails(held)
        except RuntimeError:
            logger.exception("unhandled")

    handler()

    assert _CountedRepr.reprs == 0
    text = production_sinks.read_text()
    # The traceback is still logged; only the per-frame value dump is gone.
    assert "Traceback" in text and "RuntimeError: boom" in text


def test_the_logfire_sink_does_not_repr_frame_values(monkeypatch):
    """The Logfire sink is added from logfire.loguru_handler(), which sets no diagnose."""
    shipped: list[str] = []
    monkeypatch.setenv("BASIC_MEMORY_ENV", "dev")
    # Same shape as logfire.loguru_handler(): a sink and a bare message format.
    monkeypatch.setattr(
        utils.telemetry,
        "get_logfire_handler",
        lambda: {"sink": shipped.append, "format": "{message}"},
    )
    utils.setup_logging()
    try:
        _CountedRepr.reprs = 0

        def fails(value):
            raise RuntimeError(f"boom {id(value)}")

        held = _CountedRepr()
        try:
            fails(held)
        except RuntimeError:
            logger.exception("unhandled")
    finally:
        monkeypatch.setenv("BASIC_MEMORY_ENV", "test")
        utils.setup_logging()

    assert _CountedRepr.reprs == 0
    assert any("RuntimeError: boom" in message for message in shipped)


@pytest.mark.asyncio
async def test_an_unhandled_request_exception_does_not_repr_frame_values(
    app: FastAPI, v2_project_url, production_sinks
):
    """The API's catch-all handler logs the traceback without per-frame values.

    The resolver's frame holds a counted value, standing in for the ASGI `scope` each
    middleware frame holds. Counting repr() calls is deterministic, where a wall-clock
    ceiling flaked under coverage and host load.
    """
    _CountedRepr.reprs = 0

    class ExplodingResolver:
        async def resolve_entity(self, *args, **kwargs):
            held = _CountedRepr()
            raise RuntimeError(f"resolver exploded {id(held)}")

        resolve_link = resolve_entity

    app.dependency_overrides[get_link_resolver_v2_external] = lambda: ExplodingResolver()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"{v2_project_url}/knowledge/resolve", json={"identifier": "anything"}
            )
    finally:
        app.dependency_overrides.pop(get_link_resolver_v2_external, None)

    assert response.status_code == 500
    assert "resolver exploded" in production_sinks.read_text()
    assert _CountedRepr.reprs == 0
