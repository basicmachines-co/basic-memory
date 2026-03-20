"""Telemetry coverage for MCP server lifecycle spans."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from basic_memory.mcp.server import lifespan, mcp
import basic_memory.mcp.server as server_module


@pytest.mark.asyncio
async def test_mcp_lifespan_wraps_startup_and_shutdown(config_manager) -> None:
    operations: list[tuple[str, dict]] = []

    @contextmanager
    def fake_operation(name: str, **attrs):
        operations.append((name, attrs))
        yield

    original_operation = server_module.telemetry.operation
    server_module.telemetry.operation = fake_operation
    try:
        async with lifespan(mcp):
            pass
    finally:
        server_module.telemetry.operation = original_operation

    assert [name for name, _ in operations] == [
        "mcp.lifecycle.startup",
        "mcp.lifecycle.shutdown",
    ]
