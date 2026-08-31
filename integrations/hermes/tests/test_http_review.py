"""Regression tests for the review-hardening of the Streamable HTTP mode."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from tests.test_http_provider import SERVER_URL, _enable_mcp_for_unit_test


class RetryActor:
    """HTTP actor double that fails one real call and then permits one retry."""

    instances: list["RetryActor"] = []
    fail_next_call = True

    def __init__(self, *args, **kwargs):
        self._server_url = kwargs.get("server_url")
        self.calls: list[tuple[str, dict]] = []
        self.alive = False
        self.shutdown_calls = 0
        type(self).instances.append(self)

    def start(self, timeout: float = 25.0):
        self.alive = True

    def is_alive(self):
        return self.alive

    def list_tools(self):
        return [{"name": name, "description": ""} for name in (
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
        )]

    def call(self, name, arguments, timeout=30.0):
        self.calls.append((name, dict(arguments)))
        if type(self).fail_next_call:
            type(self).fail_next_call = False
            self.alive = False
            raise RuntimeError("simulated HTTP actor disconnect")
        if name == "search_notes":
            return json.dumps({"results": [{"title": "retry note", "permalink": "main/retry"}]})
        return json.dumps({"permalink": "main/hermes-sessions/retry", "ok": True})

    def shutdown(self, timeout: float = 5.0):
        self.shutdown_calls += 1
        self.alive = False


def _write_config(tmp_path, *, url=SERVER_URL, project="main", capture=False):
    (tmp_path / "basic-memory.json").write_text(
        json.dumps(
            {
                "server_url": url,
                "project": project,
                "capture_per_turn": capture,
                "capture_session_end": False,
            }
        )
    )


def _patch_http_provider(bm, monkeypatch, tmp_path, *, capture=False):
    _enable_mcp_for_unit_test(bm, monkeypatch)
    _write_config(tmp_path, capture=capture)
    RetryActor.instances = []
    RetryActor.fail_next_call = True
    monkeypatch.setattr(bm, "_BmMcpActor", RetryActor)
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: None)
    monkeypatch.setattr(
        bm.BasicMemoryProvider,
        "_verify_project_registered",
        lambda self: pytest.fail("HTTP mode consulted the local project registry"),
    )


def test_profile_aware_hermes_home_is_used_for_availability(bm, monkeypatch, tmp_path):
    """Discovery reads the selected Hermes profile, not only ~/.hermes."""
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "basic-memory.json").write_text(json.dumps({"server_url": SERVER_URL}))
    _enable_mcp_for_unit_test(bm, monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(profile))
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: pytest.fail("looked up bm"))
    monkeypatch.setattr(bm, "_uv_binary_path", lambda: pytest.fail("looked up uv"))
    assert bm.BasicMemoryProvider().is_available() is True


def test_server_url_requires_explicit_project_name(bm, monkeypatch, tmp_path):
    """An HTTP daemon cannot silently route to a local default project."""
    _enable_mcp_for_unit_test(bm, monkeypatch)
    (tmp_path / "basic-memory.json").write_text(json.dumps({"server_url": SERVER_URL}))
    monkeypatch.setattr(bm, "_BmMcpActor", lambda *a, **k: pytest.fail("missing project connected"))
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: pytest.fail("looked up bm"))
    provider = bm.BasicMemoryProvider()
    provider.initialize(session_id="missing-project", hermes_home=str(tmp_path))
    assert provider._initialized is False


@pytest.mark.parametrize(
    ("url", "secret"),
    [
        ("ftp://127.0.0.1:8766/mcp", ""),
        ("http://", ""),
        ("http://127.0.0.1:8766/mcp?token=TOKENVALUE123", "TOKENVALUE123"),
        ("http://127.0.0.1:8766/mcp#s3cr3tfrag", "s3cr3tfrag"),
        ("http://user:UltraSecret99@127.0.0.1:8766/mcp", "UltraSecret99"),
    ],
)
def test_invalid_server_url_is_rejected_without_secret_logging(
    bm, monkeypatch, tmp_path, caplog, url, secret
):
    """Only a bare HTTP(S) endpoint is accepted; credentials never enter logs."""
    _enable_mcp_for_unit_test(bm, monkeypatch)
    _write_config(tmp_path, url=url)
    monkeypatch.setattr(bm, "_BmMcpActor", lambda *a, **k: pytest.fail("invalid URL connected"))
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: pytest.fail("invalid URL looked up bm"))
    monkeypatch.setattr(bm, "_uv_binary_path", lambda: pytest.fail("invalid URL looked up uv"))
    provider = bm.BasicMemoryProvider()
    with caplog.at_level("WARNING"):
        provider.initialize(session_id="invalid-url", hermes_home=str(tmp_path))
    assert provider._initialized is False
    if secret:
        assert secret not in caplog.text


@pytest.mark.parametrize("operation", ["tool", "prefetch"])
def test_http_call_reconnects_once_and_retries_common_paths(bm, monkeypatch, tmp_path, operation):
    """A dead HTTP actor gets exactly one bounded reconnect/retry."""
    _patch_http_provider(bm, monkeypatch, tmp_path, capture=operation == "capture")
    provider = bm.BasicMemoryProvider()
    provider.initialize(session_id="retry-session", hermes_home=str(tmp_path))
    try:
        if operation == "tool":
            raw = provider.handle_tool_call("bm_search", {"query": "retry"})
            assert "error" not in raw.lower()
        elif operation == "prefetch":
            assert "Basic Memory Recall" in provider.prefetch("retry")
        else:
            provider.sync_turn("retry user", "retry assistant")
            assert provider._sync_thread is not None
            provider._sync_thread.join(timeout=2)
            assert not provider._sync_thread.is_alive()

        assert len(RetryActor.instances) == 2
        assert RetryActor.instances[0].shutdown_calls == 1
        assert len(RetryActor.instances[0].calls) == 1
        assert len(RetryActor.instances[1].calls) == 1
    finally:
        provider.shutdown()


@pytest.mark.parametrize(
    ("hermes_tool", "arguments"),
    [
        (
            "bm_write",
            {"title": "ambiguous", "content": "do not duplicate", "folder": "tests"},
        ),
        (
            "bm_edit",
            {
                "identifier": "main/tests/existing",
                "operation": "append",
                "content": "do not duplicate",
            },
        ),
    ],
)
def test_http_failure_reconnects_but_does_not_replay_mutation(
    bm, monkeypatch, tmp_path, hermes_tool, arguments
):
    """An ambiguous write failure reconnects, but never duplicates the write."""
    _patch_http_provider(bm, monkeypatch, tmp_path)
    provider = bm.BasicMemoryProvider()
    provider.initialize(session_id="mutation-session", hermes_home=str(tmp_path))
    try:
        raw = provider.handle_tool_call(hermes_tool, arguments)
        assert "error" in raw.lower()
        assert len(RetryActor.instances) == 2
        assert len(RetryActor.instances[0].calls) == 1
        assert RetryActor.instances[0].calls[0][0] == {
            "bm_write": "write_note",
            "bm_edit": "edit_note",
        }[hermes_tool]
        assert RetryActor.instances[1].calls == []

        # The next independent call uses the fresh transport and can retry-free
        # continue operating after the ambiguous mutation result.
        recovered = provider.handle_tool_call("bm_search", {"query": "after-write"})
        assert "results" in recovered
        assert RetryActor.instances[1].calls[0][0] == "search_notes"
    finally:
        provider.shutdown()


def test_http_capture_failure_reconnects_but_does_not_replay_capture(
    bm, monkeypatch, tmp_path
):
    """Automatic capture treats a failed write as ambiguous, never as retryable."""
    _patch_http_provider(bm, monkeypatch, tmp_path, capture=True)
    provider = bm.BasicMemoryProvider()
    provider.initialize(session_id="capture-session", hermes_home=str(tmp_path))
    try:
        provider.sync_turn("captured user", "captured assistant")
        assert provider._sync_thread is not None
        provider._sync_thread.join(timeout=2)
        assert not provider._sync_thread.is_alive()
        assert len(RetryActor.instances) == 2
        assert RetryActor.instances[0].calls[0][0] == "write_note"
        assert RetryActor.instances[1].calls == []

        # A subsequent explicit read proves the reconnect was retained.
        assert "results" in provider.handle_tool_call("bm_search", {"query": "after-capture"})
        assert RetryActor.instances[1].calls[0][0] == "search_notes"
    finally:
        provider.shutdown()


def test_invalid_reinitialize_clears_actor_and_session_capture_state(
    bm, monkeypatch, tmp_path
):
    """A bad config cannot leave an old actor or note ID for a later session."""
    _patch_http_provider(bm, monkeypatch, tmp_path)
    provider = bm.BasicMemoryProvider()
    provider.initialize(session_id="before-invalid", hermes_home=str(tmp_path))
    old_actor = RetryActor.instances[0]
    provider._session_note_id = "main/hermes-sessions/old-session"
    provider._first_user_msg = "old session opener"
    provider._session_started_at = object()  # type: ignore[assignment]
    (tmp_path / "basic-memory.json").write_text(
        json.dumps({"server_url": "https://user:secret@example.test/mcp", "project": "main"})
    )

    try:
        provider.initialize(session_id="after-invalid", hermes_home=str(tmp_path))

        assert old_actor.shutdown_calls == 1
        assert provider._actor is None
        assert provider._initialized is False
        assert provider._server_url is None
        assert provider._session_id == ""
        assert provider._session_note_id is None
        assert provider._first_user_msg is None
        assert provider._session_started_at is None

        # A subsequent valid initialization must start capture with write_note,
        # never edit_note against the old session permalink.
        _write_config(tmp_path, capture=True)
        RetryActor.fail_next_call = False
        provider.initialize(session_id="after-valid", hermes_home=str(tmp_path))
        provider.sync_turn("new user", "new assistant")
        assert provider._sync_thread is not None
        provider._sync_thread.join(timeout=2)
        assert not provider._sync_thread.is_alive()
        fresh_actor = RetryActor.instances[-1]
        assert fresh_actor.calls[0][0] == "write_note"
    finally:
        provider.shutdown()


def test_stdio_call_failure_is_not_retried(bm, monkeypatch, tmp_path):
    """Automatic reconnect is scoped to server_url and cannot alter stdio."""
    class StdioFailActor(RetryActor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._server_url = None

        def call(self, name, arguments, timeout=30.0):
            self.calls.append((name, dict(arguments)))
            self.alive = False
            raise RuntimeError("simulated stdio failure")

    _enable_mcp_for_unit_test(bm, monkeypatch)
    (tmp_path / "basic-memory.json").write_text(
        json.dumps({"mode": "local", "project": "main", "capture_per_turn": False})
    )
    StdioFailActor.instances = []
    monkeypatch.setattr(bm, "_BmMcpActor", StdioFailActor)
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: "/fake/bm")
    monkeypatch.setattr(bm.BasicMemoryProvider, "_ensure_local_project", lambda self: None)
    monkeypatch.setattr(bm.BasicMemoryProvider, "_verify_project_registered", lambda self: True)
    provider = bm.BasicMemoryProvider()
    provider.initialize(session_id="stdio-failure", hermes_home=str(tmp_path))
    try:
        raw = provider.handle_tool_call("bm_search", {"query": "no retry"})
        assert "error" in raw
        assert len(StdioFailActor.instances) == 1
        assert len(StdioFailActor.instances[0].calls) == 1
    finally:
        provider.shutdown()


def test_http_transport_and_session_contexts_both_close_on_shutdown(bm, monkeypatch):
    """Shutdown unwinds ClientSession before the Streamable HTTP transport."""
    exits: list[str] = []
    transport_calls: list[tuple[str, dict]] = []
    streams = (object(), object())

    class Transport:
        def __init__(self, url, **kwargs):
            transport_calls.append((url, kwargs))

        async def __aenter__(self):
            # MCP SDK 1.x also yielded a session-id callback as a third item.
            # The plugin intentionally consumes only read/write; the SDK owns
            # Mcp-Session-Id headers and DELETE-on-close semantics.
            return (*streams, lambda: "server-session-123")

        async def __aexit__(self, exc_type, exc, tb):
            exits.append("transport")

    class Session:
        def __init__(self, read, write):
            assert (read, write) == streams

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            exits.append("session")

        async def initialize(self):
            return None

        async def list_tools(self):
            return SimpleNamespace(tools=[])

    _enable_mcp_for_unit_test(bm, monkeypatch)
    monkeypatch.setattr(bm, "streamable_http_client", lambda url: Transport(url))
    monkeypatch.setattr(bm, "ClientSession", Session, raising=False)
    actor = bm._BmMcpActor(server_url=SERVER_URL)
    actor.start(timeout=2)
    actor.shutdown(timeout=2)
    assert transport_calls == [(SERVER_URL, {})]
    assert exits == ["session", "transport"]
    assert actor._thread is not None and not actor._thread.is_alive()


def test_http_start_timeout_unwinds_contexts_and_stops_thread(bm, monkeypatch):
    """Cancellation during HTTP session startup is bounded and leak-free."""
    exits: list[str] = []

    class Transport:
        async def __aenter__(self):
            return (object(), object())

        async def __aexit__(self, exc_type, exc, tb):
            exits.append("transport")

    class HangingSession:
        def __init__(self, read, write):
            self.read = read
            self.write = write

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            exits.append("session")

        async def initialize(self):
            await asyncio.sleep(60)

        async def list_tools(self):
            return SimpleNamespace(tools=[])

    _enable_mcp_for_unit_test(bm, monkeypatch)
    monkeypatch.setattr(bm, "streamable_http_client", lambda url: Transport())
    monkeypatch.setattr(bm, "ClientSession", HangingSession, raising=False)
    actor = bm._BmMcpActor(server_url=SERVER_URL)
    with pytest.raises(TimeoutError):
        actor.start(timeout=0.05)
    assert exits == ["session", "transport"]
    assert actor._thread is not None and not actor._thread.is_alive()
