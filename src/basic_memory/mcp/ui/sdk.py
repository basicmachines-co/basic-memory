"""Helpers for embedded MCP-UI resources (Python SDK)."""

from __future__ import annotations

from typing import Any

from basic_memory.mcp.ui import load_html

try:  # Optional dependency for MCP-UI embedded resources
    from mcp_ui_server import UIMetadataKey, create_ui_resource
except ImportError:  # pragma: no cover - handled by callers
    UIMetadataKey = None
    create_ui_resource = None


class MissingMCPUIServerError(RuntimeError):
    """Raised when the MCP-UI server SDK is not available."""


def _ensure_sdk() -> tuple[Any, Any]:
    if create_ui_resource is None or UIMetadataKey is None:
        raise MissingMCPUIServerError(
            "mcp-ui-server is not installed. "
            "Install it with `uv pip install -e /Users/phernandez/dev/mcp-ui/sdks/python/server` "
            "or `pip install mcp-ui-server`."
        )
    return create_ui_resource, UIMetadataKey


def build_embedded_ui_resource(
    *,
    uri: str,
    html_filename: str,
    render_data: dict[str, Any],
    preferred_frame_size: list[str],
    metadata: dict[str, Any] | None = None,
):
    """Create an embedded UI resource using the MCP-UI Python SDK."""
    create_resource, metadata_keys = _ensure_sdk()
    html = load_html(html_filename)

    return create_resource(
        {
            "uri": uri,
            "content": {"type": "rawHtml", "htmlString": html},
            "encoding": "text",
            "uiMetadata": {
                metadata_keys.PREFERRED_FRAME_SIZE: preferred_frame_size,
                metadata_keys.INITIAL_RENDER_DATA: render_data,
            },
            "metadata": metadata or {},
        }
    )
