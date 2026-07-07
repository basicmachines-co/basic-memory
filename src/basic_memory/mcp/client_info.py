"""MCP client identity helpers for client-specific compatibility tools."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import mcp.types as mt
from fastmcp import Context
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

MCP_CLIENT_INFO_STATE_KEY = "mcp.client_info"
OPENAI_MCP_CLIENT_NAME = "openai-mcp"
ClientInfoState = dict[str, str | None]


class MCPClientInfoMiddleware(Middleware):
    """Persist sanitized initialize clientInfo in FastMCP session state."""

    async def on_initialize(
        self,
        context: MiddlewareContext[mt.InitializeRequest],
        call_next: CallNext[mt.InitializeRequest, Any],
    ) -> Any:
        result = await call_next(context)
        client_info = client_info_from_initialize(context.message)
        if client_info is not None and context.fastmcp_context is not None:
            await context.fastmcp_context.set_state(
                MCP_CLIENT_INFO_STATE_KEY,
                client_info,
            )
        return result


async def is_openai_mcp_client(context: Context | None) -> bool:
    """Return whether the current MCP session identified itself as OpenAI's MCP client."""
    if context is None:
        return False

    return client_info_is_openai_mcp(await context.get_state(MCP_CLIENT_INFO_STATE_KEY))


def client_info_from_initialize(message: mt.InitializeRequest) -> ClientInfoState | None:
    """Extract the normalized clientInfo payload from an initialize request."""
    client_info = message.params.clientInfo
    return _client_info_from_mapping(
        {
            "name": client_info.name,
            "title": client_info.title,
            "version": client_info.version,
        }
    )


def client_info_is_openai_mcp(value: object | None) -> bool:
    """Check the reported clientInfo name/title against the OpenAI MCP client label."""
    client_info = _client_info_from_mapping(value)
    if client_info is None:
        return False

    for key in ("name", "title"):
        client_value = _normalize_client_value(client_info.get(key))
        if client_value == OPENAI_MCP_CLIENT_NAME:
            return True
        if client_value is not None and client_value.startswith(f"{OPENAI_MCP_CLIENT_NAME}/"):
            return True
    return False


def _client_info_from_mapping(value: object | None) -> ClientInfoState | None:
    if not isinstance(value, Mapping):
        return None
    raw = cast(Mapping[str, object | None], value)
    normalized: ClientInfoState = {
        "name": _clean_optional_string(raw.get("name")),
        "title": _clean_optional_string(raw.get("title")),
        "version": _clean_optional_string(raw.get("version")),
    }
    if not normalized["name"] and not normalized["title"] and not normalized["version"]:
        return None
    return normalized


def _normalize_client_value(value: object | None) -> str | None:
    text = _clean_optional_string(value)
    return text.lower() if text else None


def _clean_optional_string(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
