"""Unit coverage for the daemon-backed Streamable HTTP provider mode.

The HTTP mode is deliberately tested without a ``bm`` executable, subprocess,
or project registry.  The actor is a small synchronous test double so these
tests exercise provider routing, prefetch, capture, and reconnect behavior
without requiring an MCP server.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


SERVER_URL = "http://127.0.0.1:8766/mcp"


class HttpActor:
    """Minimal actor double that records the URL and every MCP tool call."""

    instances: list["HttpActor"] = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.server_url = kwargs.get("server_url")
        if self.server_url is None:
            self.server_url = next(
                (a for a in args if isinstance(a, str) and a.startswith("http")), None
            )
        self._server_url = self.server_url
        self.calls: list[tuple[str, dict]] = []
        self.started = False
        self.alive = False
        self.shutdown_calls: list[float] = []
        type(self).instances.append(self)

    def start(self, timeout: float = 25.0) -> None:
        self.started = True
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive

    def list_tools(self) -> list[dict[str, str]]:
        return [
            {"name": name, "description": ""}
            for name in (
                "search_notes",
                "read_note",
                "write_note",
                "edit_note",
                "build_context",
                "delete_note",
                "move_note",
                "recent_activity",
                "list_memory_projects",
                "list_workspaces",
            )
        ]

    def call(self, tool_name: str, arguments: dict, timeout: float = 30.0) -> str:
        self.calls.append((tool_name, dict(arguments)))
        if tool_name == "search_notes":
            return json.dumps({"results": [{"title": "main note", "permalink": "main/note"}]})
        return json.dumps({"permalink": "main/hermes-sessions/session", "ok": True})

    def shutdown(self, timeout: float = 5.0) -> None:
        self.shutdown_calls.append(timeout)
        self.alive = False


def _enable_mcp_for_unit_test(bm, monkeypatch):
    """The test double replaces the SDK transport, so no MCP package is needed."""
    monkeypatch.setattr(bm, "_MCP_AVAILABLE", True)
    monkeypatch.setattr(bm, "streamable_http_client", object())
    # Keep this compatible with implementations that expose a separate HTTP
    # availability flag while the transport import remains optional.
    for name in ("_HTTP_AVAILABLE", "_STREAMABLE_HTTP_AVAILABLE"):
        monkeypatch.setattr(bm, name, True, raising=False)


def test_server_url_is_available_without_bm_or_uv(bm, monkeypatch):
    """Availability for URL mode must not depend on local CLI installation."""
    _enable_mcp_for_unit_test(bm, monkeypatch)
    monkeypatch.setattr(bm, "streamable_http_client", object())
    monkeypatch.setattr(bm, "_load_config", lambda _: {"server_url": SERVER_URL})
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: pytest.fail("looked up bm"))
    monkeypatch.setattr(bm, "_uv_binary_path", lambda: pytest.fail("looked up uv"))
    assert bm.BasicMemoryProvider().is_available() is True


def test_config_schema_advertises_server_url(bm):
    """The new key is discoverable without changing legacy mode choices."""
    entries = {entry["key"]: entry for entry in bm.BasicMemoryProvider().get_config_schema()}
    assert "server_url" in entries
    assert entries["server_url"]["default"] == ""


def test_actor_server_url_uses_streamable_http_transport(bm, monkeypatch):
    """The URL constructor selects Streamable HTTP, not the stdio client."""
    entered_urls: list[str] = []

    class HttpTransport:
        def __init__(self, url):
            entered_urls.append(url)

        async def __aenter__(self):
            return (object(), object())

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Session:
        def __init__(self, read, write):
            self.read = read
            self.write = write

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def initialize(self):
            return None

        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name="search_notes", description="")])

    # An async generator is not the SDK's context-manager shape; use a regular
    # factory so the fake mirrors ``streamable_http_client(url)`` exactly.
    def transport_factory(url):
        return HttpTransport(url)

    _enable_mcp_for_unit_test(bm, monkeypatch)
    monkeypatch.setattr(bm, "streamable_http_client", transport_factory)
    monkeypatch.setattr(bm, "ClientSession", Session, raising=False)
    monkeypatch.setattr(
        bm, "StdioServerParameters", lambda **kwargs: pytest.fail("used stdio"), raising=False
    )
    monkeypatch.setattr(
        bm, "stdio_client", lambda *args: pytest.fail("used stdio"), raising=False
    )

    actor = bm._BmMcpActor(server_url=SERVER_URL)
    actor.start(timeout=2.0)
    try:
        assert actor._server_url == SERVER_URL
        assert entered_urls == [SERVER_URL]
        assert actor.list_tools()[0]["name"] == "search_notes"
    finally:
        actor.shutdown(timeout=2.0)


def _http_config(tmp_path, *, capture_per_turn=False, capture_session_end=False):
    config = {
        "server_url": SERVER_URL,
        "project": "main",
        "project_path": str(tmp_path / "must-not-be-created"),
        "capture_per_turn": capture_per_turn,
        "capture_session_end": capture_session_end,
    }
    (tmp_path / "basic-memory.json").write_text(json.dumps(config))


def test_server_url_initialization_skips_bm_bootstrap_and_project_registration(
    bm, monkeypatch, tmp_path
):
    """A daemon URL is self-contained: no bm lookup, install, or project add."""
    _enable_mcp_for_unit_test(bm, monkeypatch)
    _http_config(tmp_path)
    HttpActor.instances = []

    monkeypatch.setattr(bm, "_bm_binary_path", lambda: pytest.fail("HTTP mode looked up bm"))
    monkeypatch.setattr(bm, "_uv_binary_path", lambda: pytest.fail("HTTP mode looked up uv"))
    monkeypatch.setattr(
        bm, "_install_bm_via_uv", lambda: pytest.fail("HTTP mode installed bm")
    )
    monkeypatch.setattr(
        bm.BasicMemoryProvider,
        "_ensure_local_project",
        lambda self: pytest.fail("HTTP mode created a local project"),
    )
    monkeypatch.setattr(
        bm.BasicMemoryProvider,
        "_verify_project_registered",
        lambda self: pytest.fail("HTTP mode inspected project registration"),
    )
    monkeypatch.setattr(bm, "_BmMcpActor", HttpActor)

    provider = bm.BasicMemoryProvider()
    provider.initialize(session_id="http-session", hermes_home=str(tmp_path))

    try:
        assert provider._initialized is True
        assert provider._mode == "server_url"
        assert provider._project == "main"
        assert not (tmp_path / "must-not-be-created").exists()
        assert len(HttpActor.instances) == 1
        assert HttpActor.instances[0].server_url == SERVER_URL
        assert HttpActor.instances[0].started is True
    finally:
        provider.shutdown()


def test_server_url_preserves_main_project_routing_and_prefetch(bm, monkeypatch, tmp_path):
    """HTTP calls still carry the configured ``main`` project on scoped tools."""
    _enable_mcp_for_unit_test(bm, monkeypatch)
    _http_config(tmp_path)
    HttpActor.instances = []
    monkeypatch.setattr(bm, "_BmMcpActor", HttpActor)
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: None)

    provider = bm.BasicMemoryProvider()
    provider.initialize(session_id="http-session", hermes_home=str(tmp_path))
    actor = HttpActor.instances[0]
    try:
        raw = provider.handle_tool_call("bm_search", {"query": "main"})
        assert json.loads(raw)["results"]
        assert actor.calls[0] == ("search_notes", {"project": "main", "query": "main"})

        recall = provider.prefetch("main")
        assert "Basic Memory Recall" in recall
        assert actor.calls[1][0] == "search_notes"
        assert actor.calls[1][1]["project"] == "main"
        assert actor.calls[1][1]["search_type"] == "text"
    finally:
        provider.shutdown()


def test_server_url_capture_uses_same_actor_and_project(bm, monkeypatch, tmp_path):
    """Per-turn and end-of-session capture remain ordinary MCP calls over HTTP."""
    _enable_mcp_for_unit_test(bm, monkeypatch)
    _http_config(tmp_path, capture_per_turn=True, capture_session_end=True)
    HttpActor.instances = []
    monkeypatch.setattr(bm, "_BmMcpActor", HttpActor)
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: None)

    provider = bm.BasicMemoryProvider()
    provider.initialize(session_id="http-session", hermes_home=str(tmp_path))
    provider._session_started_at = datetime(2026, 5, 10, 12, 34, tzinfo=timezone.utc)
    actor = HttpActor.instances[0]
    try:
        provider.sync_turn("user over HTTP", "assistant over HTTP")
        assert provider._sync_thread is not None
        provider._sync_thread.join(timeout=2)
        assert not provider._sync_thread.is_alive()
        assert actor.calls[0][0] == "write_note"
        assert actor.calls[0][1]["project"] == "main"

        provider.on_session_end(
            [
                {"role": "user", "content": "user over HTTP"},
                {"role": "assistant", "content": "done"},
            ]
        )
        assert any(name == "write_note" and args["project"] == "main" for name, args in actor.calls)
    finally:
        provider.shutdown()


def test_server_url_reconnects_after_dead_actor_without_cli_or_project_work(
    bm, monkeypatch, tmp_path
):
    """A dead HTTP actor is replaced with another URL-backed actor cleanly."""
    _enable_mcp_for_unit_test(bm, monkeypatch)
    _http_config(tmp_path)
    HttpActor.instances = []
    monkeypatch.setattr(bm, "_BmMcpActor", HttpActor)
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: None)
    monkeypatch.setattr(
        bm.BasicMemoryProvider,
        "_verify_project_registered",
        lambda self: pytest.fail("HTTP reconnect checked project registry"),
    )

    provider = bm.BasicMemoryProvider()
    provider.initialize(session_id="http-session", hermes_home=str(tmp_path))
    first = HttpActor.instances[0]
    first.alive = False
    provider.initialize(session_id="http-session", hermes_home=str(tmp_path))

    try:
        assert first.shutdown_calls
        assert len(HttpActor.instances) == 2
        assert HttpActor.instances[1].server_url == SERVER_URL
        assert provider._initialized is True
    finally:
        provider.shutdown()
