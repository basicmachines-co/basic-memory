"""Tests for _BmMcpActor: lifecycle, call dispatch, timeout, shutdown."""

from __future__ import annotations

import concurrent.futures
import json
import time

import pytest

from tests.conftest import FakeSession, make_scripted_actor


# ---- Lifecycle ----


def test_new_actor_is_not_running(bm):
    actor = bm._BmMcpActor(["fake-bm", "mcp"])
    assert actor._running is False


def test_start_brings_actor_up(bm):
    actor = make_scripted_actor(bm)
    actor.start(timeout=5.0)
    try:
        assert actor._running is True
        assert actor._session is not None
        assert actor._stop_future is not None
        assert actor._thread is not None and actor._thread.is_alive()
        # Tools cache populated
        names = {t["name"] for t in actor.list_tools()}
        assert "search_notes" in names
    finally:
        actor.shutdown(timeout=2.0)


def test_start_is_idempotent_when_thread_alive(bm):
    actor = make_scripted_actor(bm)
    actor.start(timeout=5.0)
    try:
        first_thread = actor._thread
        actor.start(timeout=5.0)  # second call
        assert actor._thread is first_thread  # same thread, no new one spawned
    finally:
        actor.shutdown(timeout=2.0)


def test_is_alive_tracks_actor_lifecycle(bm):
    """is_alive() is the reuse signal for initialize() — it must be False
    before start, True while the loop thread runs, and False after shutdown."""
    actor = make_scripted_actor(bm)
    assert actor.is_alive() is False
    actor.start(timeout=5.0)
    try:
        assert actor.is_alive() is True
    finally:
        actor.shutdown(timeout=2.0)
    assert actor.is_alive() is False


def test_start_init_error_raises(bm):
    boom = ValueError("BM unreachable")
    actor = make_scripted_actor(bm, raise_at_init=boom)
    with pytest.raises(RuntimeError, match="BM unreachable"):
        actor.start(timeout=5.0)
    assert actor._running is False


def test_start_timeout_raises(bm):
    """A startup timeout cancels the actor task instead of leaking its thread."""
    import asyncio

    actor = bm._BmMcpActor(["fake-bm", "mcp"])

    async def _hang_forever():
        # Never call self._ready.set(); start() should time out waiting.
        await asyncio.sleep(60)

    actor._main = _hang_forever  # type: ignore[assignment]

    with pytest.raises(TimeoutError):
        actor.start(timeout=0.5)
    assert actor._running is False
    assert actor._thread is not None
    assert not actor._thread.is_alive()


# ---- Shutdown ----


def test_shutdown_before_start_is_noop(bm):
    actor = bm._BmMcpActor(["fake-bm", "mcp"])
    # Should not raise even though nothing is running
    actor.shutdown(timeout=1.0)
    assert actor._running is False


def test_shutdown_stops_running_actor(bm):
    actor = make_scripted_actor(bm)
    actor.start(timeout=5.0)
    assert actor._thread is not None and actor._thread.is_alive()
    actor.shutdown(timeout=5.0)
    assert actor._running is False
    # Thread should exit shortly after stop_future resolves
    actor._thread.join(timeout=2.0)
    assert not actor._thread.is_alive()


def test_shutdown_is_idempotent(bm):
    actor = make_scripted_actor(bm)
    actor.start(timeout=5.0)
    actor.shutdown(timeout=2.0)
    # Second call should not raise
    actor.shutdown(timeout=2.0)
    assert actor._running is False


# ---- call() dispatch ----


def test_call_before_start_raises(bm):
    actor = bm._BmMcpActor(["fake-bm", "mcp"])
    with pytest.raises(RuntimeError):
        actor.call("search_notes", {})


def test_call_after_shutdown_raises(bm):
    actor = make_scripted_actor(bm)
    actor.start(timeout=5.0)
    actor.shutdown(timeout=2.0)
    with pytest.raises(RuntimeError, match="not running"):
        actor.call("search_notes", {})


def test_call_dispatches_through_actor_loop(bm):
    session = FakeSession(default_response={"results": [], "ok": True})
    actor = make_scripted_actor(bm, session=session)
    actor.start(timeout=5.0)
    try:
        out = actor.call("search_notes", {"query": "hi"}, timeout=5.0)
        # Output is whatever _extract_mcp_text produces; we just verify it
        # contains the response payload we configured.
        assert "ok" in out
        assert session.calls == [("search_notes", {"query": "hi"})]
    finally:
        actor.shutdown(timeout=2.0)


def test_call_returns_mcp_extracted_text(bm):
    session = FakeSession(default_response={"permalink": "p/q/r", "title": "T"})
    actor = make_scripted_actor(bm, session=session)
    actor.start(timeout=5.0)
    try:
        out = actor.call("write_note", {"title": "T"}, timeout=5.0)
        parsed = json.loads(out)
        assert parsed["permalink"] == "p/q/r"
    finally:
        actor.shutdown(timeout=2.0)


def test_call_timeout_raises_and_cancels_coroutine(bm):
    session = FakeSession(hang=True)
    actor = make_scripted_actor(bm, session=session)
    actor.start(timeout=5.0)
    try:
        t0 = time.monotonic()
        with pytest.raises(concurrent.futures.TimeoutError):
            actor.call("search_notes", {}, timeout=0.3)
        elapsed = time.monotonic() - t0
        # Sanity: we didn't accidentally wait the full session-side sleep
        assert elapsed < 2.0
        # Give the actor loop a moment to propagate the cancellation
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not session.was_cancelled:
            time.sleep(0.05)
        assert session.was_cancelled is True, (
            "Expected the underlying coroutine to be cancelled when call() times out"
        )
    finally:
        actor.shutdown(timeout=2.0)


def test_list_tools_returns_independent_copy(bm):
    actor = make_scripted_actor(bm)
    actor.start(timeout=5.0)
    try:
        snapshot = actor.list_tools()
        snapshot.append({"name": "tampered"})
        assert "tampered" not in {t["name"] for t in actor.list_tools()}
    finally:
        actor.shutdown(timeout=2.0)


def test_actor_init_error_logs_and_marks_not_running(bm, caplog):
    actor = make_scripted_actor(bm, raise_at_init=RuntimeError("server crashed"))
    with pytest.raises(RuntimeError):
        actor.start(timeout=5.0)
    assert actor._running is False
