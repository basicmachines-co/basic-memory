"""Tests for MCP clientInfo capture and classification."""

from __future__ import annotations

from typing import Any, cast

import mcp.types as mt
import pytest
from fastmcp.server.middleware import CallNext, MiddlewareContext

from basic_memory.mcp.client_info import (
    MCP_CLIENT_INFO_STATE_KEY,
    MCPClientInfoMiddleware,
    client_info_is_openai_mcp,
    is_openai_mcp_client,
)


class FakeMCPContext:
    """Small state-only context for middleware tests."""

    def __init__(self) -> None:
        self.state: dict[str, object] = {}

    async def set_state(self, key: str, value: object) -> None:
        self.state[key] = value

    async def get_state(self, key: str) -> object | None:
        return self.state.get(key)


def _initialize_context(
    *,
    name: str,
    title: str | None,
    version: str,
    fastmcp_context: object | None,
) -> MiddlewareContext[mt.InitializeRequest]:
    return MiddlewareContext(
        message=mt.InitializeRequest(
            params=mt.InitializeRequestParams(
                protocolVersion="2025-06-18",
                capabilities=mt.ClientCapabilities(),
                clientInfo=mt.Implementation(name=name, title=title, version=version),
            )
        ),
        fastmcp_context=cast(Any, fastmcp_context),
        source="client",
        type="request",
        method="initialize",
    )


def _as_initialize_next(
    callback,
) -> CallNext[mt.InitializeRequest, Any]:
    return cast(CallNext[mt.InitializeRequest, Any], callback)


@pytest.mark.asyncio
async def test_client_info_middleware_stores_initialize_state() -> None:
    """The initialize clientInfo is available to later tool calls."""
    context = FakeMCPContext()
    middleware = MCPClientInfoMiddleware()

    async def call_next(inner_context: MiddlewareContext[mt.InitializeRequest]) -> str:
        return "ok"

    result = await middleware.on_initialize(
        _initialize_context(
            name="openai-mcp",
            title=None,
            version="1.0.0",
            fastmcp_context=context,
        ),
        _as_initialize_next(call_next),
    )

    assert result == "ok"
    assert context.state[MCP_CLIENT_INFO_STATE_KEY] == {
        "name": "openai-mcp",
        "title": None,
        "version": "1.0.0",
    }


@pytest.mark.asyncio
async def test_is_openai_mcp_client_reads_session_state() -> None:
    """The gate accepts OpenAI's versioned clientInfo label."""
    context = FakeMCPContext()
    await context.set_state(
        MCP_CLIENT_INFO_STATE_KEY,
        {"name": "openai-mcp/1.0.0", "title": None, "version": None},
    )

    assert await is_openai_mcp_client(cast(Any, context)) is True


@pytest.mark.asyncio
async def test_is_openai_mcp_client_rejects_missing_context() -> None:
    """No context means no authenticated MCP clientInfo to trust."""
    assert await is_openai_mcp_client(None) is False


def test_client_info_is_openai_mcp_accepts_title() -> None:
    """Some clients place the useful label in title rather than name."""
    assert client_info_is_openai_mcp({"name": "mcp", "title": "openai-mcp", "version": "1.0.0"})


def test_client_info_is_openai_mcp_rejects_other_clients() -> None:
    """Claude/Codex-style labels do not pass the ChatGPT compatibility gate."""
    assert not client_info_is_openai_mcp({"name": "codex", "title": "Codex", "version": "5.0.0"})
    assert not client_info_is_openai_mcp({"name": None, "title": None, "version": None})
    assert not client_info_is_openai_mcp(None)
