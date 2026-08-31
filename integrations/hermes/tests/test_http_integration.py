"""Gated live tests for a running Basic Memory Streamable HTTP endpoint.

Set ``BM_SERVER_URL`` to run the read-only path, for example::

    BM_SERVER_URL=http://127.0.0.1:8766/mcp \
      uv run --with pytest --with mcp pytest tests/test_http_integration.py

No test in this module writes by default. The opt-in write test additionally
requires ``BM_HTTP_WRITE_TEST=1`` and an explicit ``BM_HTTP_PROJECT``.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest


_SERVER_URL = os.environ.get("BM_SERVER_URL", "").strip()
_PROJECT = os.environ.get("BM_HTTP_PROJECT", "main").strip() or "main"
_WRITE_TEST = os.environ.get("BM_HTTP_WRITE_TEST") == "1"
_WRITE_PROJECT = os.environ.get("BM_HTTP_PROJECT", "").strip()

try:
    import mcp  # noqa: F401

    _MCP_OK = True
except Exception:
    _MCP_OK = False

pytestmark = [
    pytest.mark.skipif(not _SERVER_URL, reason="set BM_SERVER_URL to a running MCP endpoint"),
    pytest.mark.skipif(not _MCP_OK, reason="mcp Python package not installed"),
]


@pytest.fixture
def http_provider(bm, tmp_path, monkeypatch):
    """Initialize HTTP mode without touching the local BM CLI registry."""
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: None)
    monkeypatch.setattr(
        bm, "_uv_binary_path", lambda: pytest.fail("HTTP mode attempted to locate uv")
    )
    monkeypatch.setattr(
        bm, "_install_bm_via_uv", lambda: pytest.fail("HTTP mode attempted to install bm")
    )

    config = {
        "server_url": _SERVER_URL,
        "project": _PROJECT,
        # Read-only by default: no capture write can happen during a smoke run.
        "capture_per_turn": False,
        "capture_session_end": False,
    }
    (tmp_path / "basic-memory.json").write_text(json.dumps(config))

    provider = bm.BasicMemoryProvider()
    provider.initialize(
        session_id=f"http-integration-{uuid.uuid4().hex[:8]}",
        hermes_home=str(tmp_path),
        platform="cli",
    )
    if not provider._initialized:
        pytest.fail("Provider failed to initialize against the live Streamable HTTP server")
    try:
        yield provider
    finally:
        provider.shutdown()


def test_live_http_lists_tools_and_uses_main_project(http_provider, bm):
    """Handshake and discovery work through the configured HTTP daemon."""
    names = {tool["name"] for tool in http_provider._actor.list_tools()}
    assert set(bm._HERMES_TO_BM.values()) <= names
    raw = http_provider.handle_tool_call(
        "bm_search", {"project": _PROJECT, "query": "Hermes", "limit": 1}
    )
    assert raw and "error" not in raw.lower()


def test_live_http_prefetch_is_read_only(http_provider):
    """Prefetch reaches the HTTP server without enabling capture writes."""
    result = http_provider.prefetch("Hermes", session_id="http-prefetch")
    assert isinstance(result, str)
    assert http_provider._capture_per_turn is False
    assert http_provider._capture_session_end is False


def test_live_http_clean_shutdown_and_reconnect(http_provider, tmp_path):
    """A clean close can reconnect to the same daemon URL without bm setup."""
    first_actor = http_provider._actor
    http_provider.shutdown()
    assert http_provider._initialized is False
    assert http_provider._actor is None

    http_provider.initialize(
        session_id="http-reconnect",
        hermes_home=str(tmp_path),
        platform="cli",
    )
    try:
        assert http_provider._initialized is True
        assert http_provider._actor is not first_actor
        assert http_provider._project == _PROJECT
    finally:
        http_provider.shutdown()


def test_live_http_optional_read_identifier(http_provider):
    """An explicitly supplied identifier enables a narrow live note read."""
    identifier = os.environ.get("BM_HTTP_IDENTIFIER", "").strip()
    if not identifier:
        pytest.skip("set BM_HTTP_IDENTIFIER for an explicit live note read")
    raw = http_provider.handle_tool_call("bm_read", {"identifier": identifier})
    assert raw and "error" not in raw.lower()


def test_live_http_opt_in_write_requires_marker_and_project(http_provider):
    """Writes require both an explicit marker and an explicit target project."""
    if not (_WRITE_TEST and _WRITE_PROJECT):
        pytest.skip("set BM_HTTP_WRITE_TEST=1 and BM_HTTP_PROJECT for write coverage")
    marker = f"HERMES-HTTP-WRITE-{uuid.uuid4().hex}"
    raw = http_provider.handle_tool_call(
        "bm_write",
        {
            "project": _WRITE_PROJECT,
            "title": marker,
            "content": f"# {marker}\n\nExplicit HTTP integration test marker.\n",
            "folder": "hermes-http-tests",
            "tags": ["integration", "hermes-http-test"],
        },
    )
    assert "error" not in raw.lower(), raw[:300]
