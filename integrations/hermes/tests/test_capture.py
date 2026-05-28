"""
Tests for the capture pipeline: sync_turn (per-turn) and on_session_end (summary).

These tests run the *real* sync_turn code path with a mocked actor, so threading
and argument-shape regressions are caught.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest


def _provider_with_mock_actor(bm, *, project="test-proj", capture_folder="hermes-sessions"):
    p = bm.BasicMemoryProvider()
    p._initialized = True
    p._project = project
    p._capture_folder = capture_folder
    p._session_id = "20260510_123456_abcdef"
    p._session_started_at = datetime(2026, 5, 10, 12, 34, 56, tzinfo=timezone.utc)
    actor = MagicMock()
    p._actor = actor
    return p, actor


def _wait_for_thread(p, attr="_sync_thread", timeout=5.0):
    t = getattr(p, attr)
    if t is not None:
        t.join(timeout=timeout)
        assert not t.is_alive(), f"{attr} did not finish within {timeout}s"


# ---- sync_turn first-turn path: write_note ----


def test_sync_turn_first_turn_calls_write_note(bm):
    p, actor = _provider_with_mock_actor(bm)
    actor.call.return_value = json.dumps(
        {
            "permalink": "test-proj/hermes-sessions/hermes-session-2026-05-10-1234-abcdef",
            "title": "Hermes Session 2026-05-10 1234 abcdef",
        }
    )

    p.sync_turn("hello", "hi back")
    _wait_for_thread(p, "_sync_thread")

    actor.call.assert_called_once()
    bm_tool, bm_args = actor.call.call_args[0][:2]
    assert bm_tool == "write_note"
    assert bm_args["project"] == "test-proj"
    assert bm_args["directory"] == "hermes-sessions"
    assert "Hermes Session" in bm_args["title"]
    assert "hello" in bm_args["content"]
    assert "hi back" in bm_args["content"]
    assert "## Turns" in bm_args["content"]
    assert bm_args["output_format"] == "json"
    assert "hermes-session" in bm_args["tags"]


def test_sync_turn_first_turn_stores_extracted_permalink(bm):
    p, actor = _provider_with_mock_actor(bm)
    actor.call.return_value = json.dumps(
        {
            "permalink": "test-proj/hermes-sessions/hermes-session-foo",
            "title": "T",
        }
    )

    p.sync_turn("u", "a")
    _wait_for_thread(p, "_sync_thread")

    assert p._session_note_id == "test-proj/hermes-sessions/hermes-session-foo"


def test_sync_turn_records_first_user_message(bm):
    p, actor = _provider_with_mock_actor(bm)
    actor.call.return_value = json.dumps({"permalink": "x"})

    p.sync_turn("the very first user message", "reply")
    _wait_for_thread(p, "_sync_thread")

    assert p._first_user_msg == "the very first user message"

    # A subsequent first-user-msg call should NOT overwrite the original
    p.sync_turn("a much later user message", "another reply")
    _wait_for_thread(p, "_sync_thread")
    assert p._first_user_msg == "the very first user message"


# ---- sync_turn append path: edit_note ----


def test_sync_turn_subsequent_turn_calls_edit_note_append(bm):
    p, actor = _provider_with_mock_actor(bm)
    p._session_note_id = "test-proj/hermes-sessions/already-exists"

    p.sync_turn("turn 2 user", "turn 2 assistant")
    _wait_for_thread(p, "_sync_thread")

    actor.call.assert_called_once()
    bm_tool, bm_args = actor.call.call_args[0][:2]
    assert bm_tool == "edit_note"
    assert bm_args["identifier"] == "test-proj/hermes-sessions/already-exists"
    assert bm_args["operation"] == "append"
    assert "turn 2 user" in bm_args["content"]
    assert "turn 2 assistant" in bm_args["content"]


def test_sync_turn_session_note_id_stable_across_turns(bm):
    p, actor = _provider_with_mock_actor(bm)
    actor.call.return_value = json.dumps({"permalink": "test-proj/folder/note-perma"})

    p.sync_turn("u1", "a1")
    _wait_for_thread(p, "_sync_thread")
    first_id = p._session_note_id

    # Reconfigure mock to return something different — should be IGNORED for
    # the second turn since we're now using the existing permalink to append.
    actor.call.return_value = json.dumps({"permalink": "wrong-id"})

    p.sync_turn("u2", "a2")
    _wait_for_thread(p, "_sync_thread")

    assert p._session_note_id == first_id, "session_note_id mutated on second turn"
    # And the second call was edit_note, not write_note
    second_call_tool = actor.call.call_args_list[1][0][0]
    assert second_call_tool == "edit_note"


# ---- sync_turn gating ----


def test_sync_turn_skipped_when_capture_per_turn_off(bm):
    p, actor = _provider_with_mock_actor(bm)
    p._capture_per_turn = False
    p.sync_turn("u", "a")
    actor.call.assert_not_called()


def test_sync_turn_skipped_when_uninitialized(bm):
    p = bm.BasicMemoryProvider()
    p._actor = MagicMock()
    p.sync_turn("u", "a")
    p._actor.call.assert_not_called()


def test_sync_turn_skipped_when_actor_none(bm):
    p, _ = _provider_with_mock_actor(bm)
    p._actor = None
    p.sync_turn("u", "a")  # should not raise


def test_sync_turn_skipped_when_circuit_open(bm):
    import time as _time

    p, actor = _provider_with_mock_actor(bm)
    p._failure_pause_until = _time.monotonic() + 60.0
    p.sync_turn("u", "a")
    actor.call.assert_not_called()


def test_sync_turn_records_failure_on_actor_exception(bm):
    p, actor = _provider_with_mock_actor(bm)
    actor.call.side_effect = RuntimeError("boom")

    p.sync_turn("u", "a")
    _wait_for_thread(p, "_sync_thread")

    assert p._failure_count >= 1


def test_sync_turn_thread_is_daemonic(bm):
    p, actor = _provider_with_mock_actor(bm)
    actor.call.return_value = json.dumps({"permalink": "x"})
    p.sync_turn("u", "a")
    assert p._sync_thread is not None
    assert p._sync_thread.daemon is True
    _wait_for_thread(p, "_sync_thread")


# ---- _capture_turn directly (no thread) ----


def test_capture_turn_first_call_writes_note_with_session_metadata(bm):
    p, actor = _provider_with_mock_actor(bm)
    actor.call.return_value = json.dumps({"permalink": "p/folder/note"})
    p._capture_turn("u msg", "a msg")
    bm_tool, bm_args = actor.call.call_args[0][:2]
    assert bm_tool == "write_note"
    assert "20260510_123456_abcdef" in bm_args["content"]
    # Auto-captured banner present
    assert "Auto-captured" in bm_args["content"]


def test_capture_turn_truncates_huge_messages(bm):
    p, actor = _provider_with_mock_actor(bm)
    actor.call.return_value = json.dumps({"permalink": "p/x"})
    huge = "X" * 10000
    p._capture_turn(huge, huge)
    bm_args = actor.call.call_args[0][1]
    # Body should not contain 10k Xs verbatim — _truncate caps at 4000
    assert bm_args["content"].count("X") < 9000
    assert "..." in bm_args["content"]


# ---- on_session_end summary ----


def test_on_session_end_writes_summary_note(bm):
    p, actor = _provider_with_mock_actor(bm)
    p._session_note_id = "p/folder/transcript"
    actor.call.return_value = json.dumps({"permalink": "p/folder/summary"})

    messages = [
        {"role": "user", "content": "first user message"},
        {"role": "assistant", "content": "first assistant"},
        {"role": "user", "content": "second user"},
        {"role": "assistant", "content": "last assistant message"},
    ]
    p.on_session_end(messages)

    actor.call.assert_called_once()
    bm_tool, bm_args = actor.call.call_args[0][:2]
    assert bm_tool == "write_note"
    assert "Hermes Session Summary" in bm_args["title"]
    assert bm_args["directory"] == "hermes-sessions"
    assert "first user message" in bm_args["content"]
    assert "last assistant message" in bm_args["content"]
    # Summary should link back to the transcript via Relations when
    # session_note_id is known
    assert "summary_of [[p/folder/transcript]]" in bm_args["content"]


def test_on_session_end_omits_relations_when_no_transcript_id(bm):
    p, actor = _provider_with_mock_actor(bm)
    p._session_note_id = None
    actor.call.return_value = json.dumps({"permalink": "p/folder/summary"})

    p.on_session_end([{"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}])

    bm_args = actor.call.call_args[0][1]
    assert "## Relations" not in bm_args["content"]


def test_on_session_end_handles_list_of_dicts_content(bm):
    """OpenAI-style content blocks: list of {type: text, text: ...}."""
    p, actor = _provider_with_mock_actor(bm)
    actor.call.return_value = json.dumps({"permalink": "p/x"})

    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hello world"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "goodbye"}]},
    ]
    p.on_session_end(messages)
    bm_args = actor.call.call_args[0][1]
    assert "hello world" in bm_args["content"]
    assert "goodbye" in bm_args["content"]


def test_on_session_end_uses_first_user_msg_when_set(bm):
    p, actor = _provider_with_mock_actor(bm)
    p._first_user_msg = "captured-via-sync_turn"
    actor.call.return_value = json.dumps({"permalink": "p/x"})

    messages = [
        {"role": "user", "content": "in-the-messages-list"},
        {"role": "assistant", "content": "ok"},
    ]
    p.on_session_end(messages)
    bm_args = actor.call.call_args[0][1]
    # _first_user_msg takes priority over messages list
    assert "captured-via-sync_turn" in bm_args["content"]


def test_on_session_end_handles_empty_messages(bm):
    p, actor = _provider_with_mock_actor(bm)
    actor.call.return_value = json.dumps({"permalink": "p/x"})
    p.on_session_end([])
    bm_args = actor.call.call_args[0][1]
    assert "(no user message)" in bm_args["content"]
    assert "(no assistant message)" in bm_args["content"]
    assert "0 user / 0 assistant" in bm_args["content"]


def test_on_session_end_skipped_when_disabled(bm):
    p, actor = _provider_with_mock_actor(bm)
    p._capture_session_end = False
    p.on_session_end([{"role": "user", "content": "u"}])
    actor.call.assert_not_called()


def test_on_session_end_skipped_when_uninitialized(bm):
    p = bm.BasicMemoryProvider()
    p._actor = MagicMock()
    p.on_session_end([{"role": "user", "content": "u"}])
    p._actor.call.assert_not_called()


def test_on_session_end_logs_and_swallows_errors(bm, caplog):
    p, actor = _provider_with_mock_actor(bm)
    actor.call.side_effect = RuntimeError("BM down")
    # Should not raise — the summary is best-effort
    p.on_session_end([{"role": "user", "content": "u"}])


# ---- shutdown lifecycle ----


def test_shutdown_clears_initialized_flag(bm):
    p, actor = _provider_with_mock_actor(bm)
    p.shutdown()
    assert p._initialized is False
    assert p._actor is None
    actor.shutdown.assert_called_once()


def test_shutdown_swallows_actor_errors(bm):
    p, actor = _provider_with_mock_actor(bm)
    actor.shutdown.side_effect = RuntimeError("bad")
    # Must not raise
    p.shutdown()
    assert p._initialized is False


def test_shutdown_idempotent(bm):
    p, _ = _provider_with_mock_actor(bm)
    p.shutdown()
    p.shutdown()  # should not raise


def test_shutdown_before_initialize_is_noop(bm):
    p = bm.BasicMemoryProvider()
    p.shutdown()  # _actor is None, shouldn't error
