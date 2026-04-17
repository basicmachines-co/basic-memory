"""Telemetry coverage for MCP server lifecycle spans."""

from __future__ import annotations

from contextlib import contextmanager

import logfire
import pytest

from basic_memory.mcp.server import lifespan, mcp


@pytest.mark.asyncio
async def test_mcp_lifespan_wraps_startup_and_shutdown(config_manager, monkeypatch) -> None:
    spans: list[tuple[str, dict]] = []

    @contextmanager
    def fake_span(name: str, **attrs):
        spans.append((name, attrs))
        yield

    monkeypatch.setattr(logfire, "span", fake_span)

    async with lifespan(mcp):
        pass

    span_names = [name for name, _ in spans]
    assert "mcp.lifecycle.startup" in span_names
    assert "mcp.lifecycle.shutdown" in span_names
