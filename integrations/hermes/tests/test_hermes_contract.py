"""
Hermes v0.22 memory-provider contract.

Hermes v0.22 builds one provider instance per agent and profile, runs sync_turn
on the memory manager's single background worker, rotates session ids with
on_session_switch, passes turn_author, and delegates register_command. These
tests pin the plugin to that contract and to the reliability fixes that ride
with it (reconnect after a lost `bm mcp`, recall previews and indicator).
"""

from __future__ import annotations

import json
import threading
import weakref
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from .conftest import FakeSession, make_scripted_actor


def _provider(bm, *, project="hermes-memory"):
    p = bm.BasicMemoryProvider()
    p._initialized = True
    p._project = project
    p._session_id = "20261101_120000_abc123"
    p._session_started_at = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    actor = MagicMock()
    actor.is_alive.return_value = True
    p._actor = actor
    return p, actor


# --- Recall previews and the recall indicator ---


def test_snippet_starts_near_the_first_query_term(bm):
    body = "# Hermes Session\n\nLive transcript header. " + "filler " * 60 + "zebra rollout Friday."
    out = bm._snippet_around(body, "what did we decide about the zebra rollout", width=120)
    assert "zebra rollout" in out
    assert out.startswith("…")


def test_snippet_falls_back_to_the_opening(bm):
    assert bm._snippet_around("Opening line here.", "nomatch", width=50) == "Opening line here."
    assert bm._snippet_around("", "x") == ""


def test_recall_preview_shows_why_a_transcript_matched(bm):
    p, actor = _provider(bm)
    actor.call.return_value = json.dumps(
        {
            "results": [
                {
                    "title": "Hermes Session",
                    "permalink": "s",
                    "content": "# Hermes Session\n\nLive transcript. "
                    + "x " * 200
                    + "The zebra migration ships Friday.",
                }
            ]
        }
    )
    out = p.prefetch("zebra migration")
    assert "zebra migration ships Friday" in out
    assert actor.call.call_args[0][1]["mode"] == "text"


def test_recall_status_reflects_only_the_last_prefetch(bm):
    p, actor = _provider(bm)
    actor.call.return_value = json.dumps(
        {"results": [{"title": "A", "permalink": "a"}, {"title": "B", "permalink": "b"}]}
    )
    p.prefetch("query")
    status = p.recall_status()
    assert status.provider_label == "Basic Memory" and status.count == 2
    actor.call.return_value = json.dumps({"results": []})
    p.prefetch("query")
    assert p.recall_status() is None


def test_cached_prefetch_reports_its_own_count(bm):
    p, _ = _provider(bm)
    p._pending_prefetch, p._pending_prefetch_count = "## Basic Memory Recall\n- x", 1
    assert p.prefetch("q").startswith("## Basic Memory Recall")
    assert p.recall_status().count == 1


def test_queue_prefetch_runs_in_the_callers_profile_context(bm, monkeypatch):
    spawned = []

    def fake_spawn(target, *, name, daemon):
        spawned.append(name)
        return threading.Thread(target=target, name=name, daemon=daemon)

    monkeypatch.setattr(bm, "_hermes_spawn_context_thread", fake_spawn)
    p, actor = _provider(bm)
    actor.call.return_value = json.dumps({"results": [{"title": "T", "permalink": "t"}]})
    p.queue_prefetch("zebra")
    p._prefetch_thread.join(5)
    assert spawned == ["bm-prefetch"]
    assert p._pending_prefetch_count == 1


def test_spawn_thread_without_hermes_022_uses_a_plain_daemon_thread(bm, monkeypatch):
    monkeypatch.setattr(bm, "_hermes_spawn_context_thread", None)
    ran = threading.Event()
    thread = bm._spawn_thread(ran.set, name="t")
    thread.join(5)
    assert ran.is_set() and thread.daemon


# --- Capture ---


def test_sync_turn_accepts_hermes_022_keywords_and_names_the_author(bm):
    p, actor = _provider(bm)
    actor.call.return_value = json.dumps({"permalink": "x"})
    p.sync_turn(
        "hey",
        "hello",
        session_id="s",
        messages=[{"role": "user"}],
        turn_author={"id": "1", "name": "Drew", "is_bot": False},
    )
    assert "**User (Drew):** hey" in actor.call.call_args[0][1]["content"]


@pytest.mark.parametrize("context", ["subagent", "cron", "flush"])
def test_non_primary_agents_do_not_capture(bm, context):
    p, actor = _provider(bm)
    p._agent_context = context
    p.sync_turn("u", "a")
    p.on_session_end([{"role": "user", "content": "u"}])
    actor.call.assert_not_called()


def test_capture_appends_when_the_transcript_title_already_exists(bm):
    p, actor = _provider(bm)
    actor.call.side_effect = [
        json.dumps({"error": "ALREADY_EXISTS: A note already exists"}),
        "Edited hermes-sessions/x.md (append)",
    ]
    p.sync_turn("hello", "hi")
    assert [c[0][0] for c in actor.call.call_args_list] == ["bm_write", "bm_edit"]
    assert p._session_note_id.endswith(".md")


def test_session_summary_replaces_an_earlier_one(bm):
    p, actor = _provider(bm)
    actor.call.return_value = json.dumps({"permalink": "x"})
    p.on_session_end([{"role": "user", "content": "u"}])
    assert actor.call.call_args[0][1]["overwrite"] is True


# --- Session switching ---


def test_reset_starts_a_fresh_transcript(bm):
    p, actor = _provider(bm)
    p._session_note_id = "hermes-memory/hermes-sessions/old"
    p._first_user_msg = "old opener"
    p._pending_prefetch = "## stale recall"
    p.on_session_switch("20261101_130000_def456", reset=True)
    assert p._session_id == "20261101_130000_def456"
    assert p._session_note_id is None and p._first_user_msg is None
    assert p._continues_note_id is None and p._pending_prefetch == ""
    actor.call.return_value = json.dumps({"permalink": "hermes-memory/hermes-sessions/new"})
    p.sync_turn("fresh", "start")
    tool, args = actor.call.call_args[0][:2]
    assert tool == "bm_write" and "def456" in args["title"]
    assert "continues" not in args["content"]


def test_continuation_links_the_new_transcript_to_the_old(bm):
    p, actor = _provider(bm)
    p._session_note_id = "hermes-memory/hermes-sessions/old"
    p.on_session_switch("20261101_130000_def456", parent_session_id="20261101_120000_abc123")
    actor.call.return_value = json.dumps({"permalink": "x"})
    p.sync_turn("go on", "ok")
    content = actor.call.call_args[0][1]["content"]
    assert "- continues [[hermes-memory/hermes-sessions/old]]" in content


def test_rewind_and_same_id_keep_the_transcript(bm):
    p, _ = _provider(bm)
    p._session_note_id = "keep"
    p.on_session_switch(p._session_id, rewound=True)
    p.on_session_switch(p._session_id)
    p.on_session_switch("")
    assert p._session_note_id == "keep"


# --- Reliability ---


def test_lost_connection_restarts_bm_mcp_and_retries(bm, monkeypatch):
    p, dead = _provider(bm)
    dead.call.side_effect = RuntimeError("Connection closed")
    dead.is_alive.return_value = False
    fresh = MagicMock()
    fresh.call.return_value = json.dumps({"results": []})

    def fake_initialize(session_id, **kwargs):
        assert session_id == p._session_id  # same session: keep its transcript
        p._actor, p._initialized = fresh, True

    monkeypatch.setattr(p, "initialize", fake_initialize)
    assert json.loads(p.handle_tool_call("bm_search", {"query": "x"})) == {"results": []}
    dead.shutdown.assert_called_once()
    assert p._actor is fresh


def test_restart_is_rate_limited(bm, monkeypatch):
    p, dead = _provider(bm)
    dead.call.side_effect = RuntimeError("Connection closed")
    dead.is_alive.return_value = False
    starts = []
    monkeypatch.setattr(p, "initialize", lambda *a, **k: starts.append(1))
    p.handle_tool_call("bm_search", {"query": "x"})
    p._actor, p._initialized = dead, True
    p.handle_tool_call("bm_search", {"query": "x"})
    assert len(starts) == 1


def test_restart_defers_to_a_replacement_another_thread_made(bm):
    p, dead = _provider(bm)
    replacement = MagicMock()
    replacement.is_alive.return_value = True
    p._actor = replacement
    assert p._restart(dead) is True


def test_ordinary_errors_do_not_restart(bm, monkeypatch):
    p, actor = _provider(bm)
    actor.call.side_effect = ValueError("bad argument")
    monkeypatch.setattr(p, "initialize", lambda *a, **k: pytest.fail("must not restart"))
    assert "error" in json.loads(p.handle_tool_call("bm_search", {"query": "x"}))


def test_shutdown_during_a_call_does_not_restart_bm_mcp(bm, monkeypatch):
    """Stopping the actor closes the connection under an in-flight call.

    That call must fail. Restarting `bm mcp` for it would start a process that
    shutdown() then drops without stopping it.
    """
    p, actor = _provider(bm)
    in_flight, connection_closed = threading.Event(), threading.Event()

    def call_until_the_connection_closes(tool, args, timeout):
        in_flight.set()
        connection_closed.wait(5)
        raise RuntimeError("Connection closed")

    def stop_actor(timeout=5.0):
        actor.is_alive.return_value = False
        connection_closed.set()
        # The real actor joins its loop thread here, so the failed call handles
        # its error while shutdown() is still waiting on the actor.
        if threading.current_thread() is not caller:
            caller.join(5)

    actor.call.side_effect = call_until_the_connection_closes
    actor.shutdown.side_effect = stop_actor
    replacements = []

    def restart_bm_mcp(session_id, **kwargs):
        fresh = MagicMock()
        fresh.call.return_value = json.dumps({"results": []})
        replacements.append(fresh)
        p._actor, p._initialized = fresh, True

    monkeypatch.setattr(p, "initialize", restart_bm_mcp)
    results = []
    caller = threading.Thread(
        target=lambda: results.append(p.handle_tool_call("bm_search", {"query": "x"})),
        daemon=True,
    )
    caller.start()
    assert in_flight.wait(5)

    p.shutdown()
    caller.join(5)

    assert replacements == [], "a provider that is shutting down started a new bm mcp"
    assert "error" in json.loads(results[0])
    assert p._actor is None


def test_shutdown_waits_for_a_restart_in_progress_and_stops_its_replacement(bm, monkeypatch):
    """`bm mcp` died first and a restart is already running when shutdown() starts.

    shutdown() lets that restart finish, then stops the replacement it made.
    """
    p, dead = _provider(bm)
    dead.call.side_effect = RuntimeError("Connection closed")
    dead.is_alive.return_value = False
    fresh = MagicMock()
    fresh.call.return_value = json.dumps({"results": []})
    restarting, finish_restart = threading.Event(), threading.Event()

    def slow_restart(session_id, **kwargs):
        restarting.set()
        finish_restart.wait(5)
        p._actor, p._initialized = fresh, True

    monkeypatch.setattr(p, "initialize", slow_restart)
    caller = threading.Thread(
        target=p.handle_tool_call, args=("bm_search", {"query": "x"}), daemon=True
    )
    caller.start()
    assert restarting.wait(5)

    closer = threading.Thread(target=p.shutdown, daemon=True)
    closer.start()
    closer.join(0.2)
    assert closer.is_alive(), "shutdown() did not wait for the restart in progress"

    finish_restart.set()
    caller.join(5)
    closer.join(5)
    fresh.shutdown.assert_called_once()
    assert p._actor is None


def test_closed_stream_errors_count_as_a_lost_connection(bm):
    actor = MagicMock()
    actor.is_alive.return_value = True
    closed = type("ClosedResourceError", (Exception,), {})()
    assert bm._is_connection_lost(closed, actor)
    assert not bm._is_connection_lost(ValueError("nope"), actor)


def test_call_without_an_actor_fails_fast(bm):
    p = bm.BasicMemoryProvider()
    with pytest.raises(RuntimeError, match="not started"):
        p._call("bm_search", {}, timeout=1.0)


def test_success_clears_scattered_failures(bm):
    p, actor = _provider(bm)
    p._failure_count = 4
    actor.call.return_value = json.dumps({"results": []})
    p.handle_tool_call("bm_search", {"query": "x"})
    assert p._failure_count == 0


# --- Registration and per-profile slash commands ---


class _DelegatingCtx:
    """Hermes v0.22's collector: forwards every register_* call."""

    def __init__(self):
        self.commands = {}
        self.skills = []

    def register_memory_provider(self, provider):
        self.provider = provider

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands[name] = handler

    def register_skill(self, name, path, description=""):
        self.skills.append(name)


def test_modern_hermes_registers_through_ctx_and_skips_the_reach_in(bm, monkeypatch):
    monkeypatch.setattr(
        bm,
        "_register_via_plugin_manager",
        lambda *a, **k: pytest.fail("reach-in used on Hermes v0.22"),
    )
    ctx = _DelegatingCtx()
    bm.register(ctx)
    assert len(ctx.commands) == 8 and ctx.skills == ["basic-memory"]


def test_slash_commands_use_the_profiles_live_provider(bm, monkeypatch, tmp_path):
    session = FakeSession(default_response={"results": [{"title": "Hit", "permalink": "p/hit"}]})
    actor = make_scripted_actor(bm, session=session)
    actor.start()
    try:
        live = bm.BasicMemoryProvider()
        live._actor, live._initialized, live._hermes_home = actor, True, str(tmp_path)
        monkeypatch.setitem(bm._live_providers, bm._home_key(tmp_path), weakref.ref(live))
        monkeypatch.setattr(bm, "_current_hermes_home", lambda: str(tmp_path))
        handlers = {n: h for n, h, _, _ in bm._build_slash_commands()}
        assert "Hit" in handlers["bm-search"]("anything")
        assert session.calls, "the live provider's bm mcp was not used"
        status = handlers["bm-status"]("")
        assert "Status:      ready" in status and "MCP process: running" in status
    finally:
        actor.shutdown()


def test_slash_provider_binds_to_the_callers_profile_home(bm, monkeypatch, tmp_path):
    seen = {}

    def fake_initialize(self, session_id, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(bm.BasicMemoryProvider, "initialize", fake_initialize)
    monkeypatch.setattr(bm, "_current_hermes_home", lambda: str(tmp_path / "profile-b"))
    monkeypatch.setattr(bm, "_slash_providers", {})
    handlers = {n: h for n, h, _, _ in bm._build_slash_commands()}
    assert "not initialized" in handlers["bm-search"]("x")
    assert seen["hermes_home"] == str(tmp_path / "profile-b")
    assert seen["agent_context"] == "slash"


def test_status_without_a_live_provider_does_not_start_bm(bm, monkeypatch, tmp_path):
    monkeypatch.setattr(bm, "_current_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(bm, "_slash_providers", {})
    monkeypatch.setattr(
        bm.BasicMemoryProvider, "initialize", lambda *a, **k: pytest.fail("started bm")
    )
    monkeypatch.setattr(bm, "_MCP_AVAILABLE", True)
    monkeypatch.setattr(bm, "_bm_binary_path", lambda: "/fake/bin/bm")
    out = {n: h for n, h, _, _ in bm._build_slash_commands()}["bm-status"]("")
    assert "Status:      ready" in out
    assert "MCP process: idle (starts on the next /bm-* command or agent session)" in out


def test_current_hermes_home_falls_back_to_the_environment(bm, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert bm._current_hermes_home() == str(tmp_path)


def test_ready_registers_and_shutdown_unregisters_the_live_provider(bm, tmp_path):
    p, actor = _provider(bm)
    p._hermes_home = str(tmp_path)
    p._mark_session_ready(p._session_id)
    assert bm._live_provider_for(tmp_path) is p
    p.shutdown()
    assert bm._home_key(tmp_path) not in bm._live_providers
    assert bm._live_provider_for(tmp_path) is None
