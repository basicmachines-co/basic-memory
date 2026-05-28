"""Tests for prefetch / queue_prefetch / _format_prefetch."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest


def _initialized_provider(bm):
    p = bm.BasicMemoryProvider()
    p._initialized = True
    p._project = "test-proj"
    p._actor = MagicMock()
    return p


# ---- prefetch ----


def test_prefetch_returns_cached_value_drained(bm):
    p = _initialized_provider(bm)
    p._pending_prefetch = "## cached recall"
    out = p.prefetch("any query")
    assert out == "## cached recall"
    # Cache must be drained so the next prefetch doesn't return stale results
    assert p._pending_prefetch == ""
    p._actor.call.assert_not_called()


def test_prefetch_calls_search_when_cache_empty(bm):
    p = _initialized_provider(bm)
    p._actor.call.return_value = json.dumps(
        {"results": [{"title": "T", "permalink": "p/t", "content": "c"}]}
    )
    out = p.prefetch("hello world")
    assert "## Basic Memory Recall" in out
    assert "**T**" in out
    p._actor.call.assert_called_once()
    bm_tool, bm_args = p._actor.call.call_args[0][:2]
    assert bm_tool == "search_notes"
    assert bm_args["query"] == "hello world"
    assert bm_args["page_size"] == 5
    assert bm_args["output_format"] == "json"
    # Pin search_type=text so BM doesn't fall into the hybrid+async-vector
    # path on the prefetch hot path. See prefetch() comment for rationale.
    assert bm_args["search_type"] == "text"


def test_prefetch_returns_empty_when_uninitialized(bm):
    p = bm.BasicMemoryProvider()
    assert p.prefetch("x") == ""


def test_prefetch_returns_empty_when_circuit_open(bm):
    p = _initialized_provider(bm)
    p._failure_pause_until = time.monotonic() + 60.0
    assert p.prefetch("x") == ""
    p._actor.call.assert_not_called()


def test_prefetch_records_failure_on_actor_error(bm):
    p = _initialized_provider(bm)
    p._actor.call.side_effect = RuntimeError("boom")
    assert p.prefetch("x") == ""
    assert p._failure_count == 1


def test_prefetch_returns_empty_for_empty_results(bm):
    p = _initialized_provider(bm)
    p._actor.call.return_value = json.dumps({"results": []})
    assert p.prefetch("x") == ""


# ---- queue_prefetch ----


def test_queue_prefetch_fills_cache_in_background(bm):
    p = _initialized_provider(bm)
    p._actor.call.return_value = json.dumps(
        {"results": [{"title": "Bg", "permalink": "p/bg", "content": "c"}]}
    )
    p.queue_prefetch("user typed something")
    # Wait for the daemon thread to finish
    if p._prefetch_thread:
        p._prefetch_thread.join(timeout=5.0)
    # Cache should now have the formatted result
    assert "**Bg**" in p._pending_prefetch
    # And subsequent prefetch returns it without making another call
    p._actor.call.reset_mock()
    out = p.prefetch("anything")
    assert "**Bg**" in out
    p._actor.call.assert_not_called()


def test_queue_prefetch_uses_longer_timeout_than_sync_prefetch(bm):
    """queue_prefetch runs in background, so it can afford a longer timeout."""
    p = _initialized_provider(bm)
    p._actor.call.return_value = json.dumps({"results": []})
    p.queue_prefetch("q")
    if p._prefetch_thread:
        p._prefetch_thread.join(timeout=5.0)
    timeout = p._actor.call.call_args.kwargs.get("timeout") or p._actor.call.call_args[1].get(
        "timeout"
    )
    # Background prefetch is more patient than the foreground 3.0s
    assert timeout is not None and timeout > 3.0


def test_queue_prefetch_skipped_when_thread_in_flight(bm):
    p = _initialized_provider(bm)

    # Simulate an already-running prefetch thread
    class _StillAlive:
        def is_alive(self):
            return True

    p._prefetch_thread = _StillAlive()  # type: ignore[assignment]
    p.queue_prefetch("q")
    p._actor.call.assert_not_called()


def test_queue_prefetch_skipped_when_circuit_open(bm):
    p = _initialized_provider(bm)
    p._failure_pause_until = time.monotonic() + 60.0
    p.queue_prefetch("q")
    p._actor.call.assert_not_called()


def test_queue_prefetch_skipped_when_uninitialized(bm):
    p = bm.BasicMemoryProvider()
    p._actor = MagicMock()
    p.queue_prefetch("q")
    p._actor.call.assert_not_called()


def test_queue_prefetch_records_failure_on_bg_error(bm):
    p = _initialized_provider(bm)
    p._actor.call.side_effect = RuntimeError("backend down")
    p.queue_prefetch("q")
    if p._prefetch_thread:
        p._prefetch_thread.join(timeout=5.0)
    assert p._failure_count >= 1


# ---- _format_prefetch ----


def _format(bm, payload):
    """Helper: call _format_prefetch on a fresh provider."""
    return bm.BasicMemoryProvider()._format_prefetch(payload)


def test_format_prefetch_with_results(bm):
    payload = json.dumps(
        {
            "results": [
                {"title": "A", "permalink": "p/a", "content": "first line"},
                {"title": "B", "permalink": "p/b", "content": "second line"},
            ]
        }
    )
    out = _format(bm, payload)
    assert "## Basic Memory Recall" in out
    assert "**A**" in out and "**B**" in out
    assert "p/a" in out and "p/b" in out


def test_format_prefetch_caps_at_5_entries(bm):
    results = [{"title": f"T{i}", "permalink": f"p/{i}", "content": "x"} for i in range(20)]
    payload = json.dumps({"results": results})
    out = _format(bm, payload)
    # Five lines + one heading = 6 lines max
    assert out.count("\n- **") == 5


def test_format_prefetch_caps_preview_length(bm):
    payload = json.dumps(
        {
            "results": [
                {"title": "T", "permalink": "p/t", "content": "x" * 5000},
            ]
        }
    )
    out = _format(bm, payload)
    # Each result line includes the preview, capped to 200 chars
    line = [l for l in out.split("\n") if l.startswith("- ")][0]
    # Some boilerplate around the preview, but the long preview is capped
    assert len(line) < 400


def test_format_prefetch_collapses_whitespace(bm):
    payload = json.dumps(
        {
            "results": [
                {"title": "T", "permalink": "p/t", "content": "first\n\n  second\tthird"},
            ]
        }
    )
    out = _format(bm, payload)
    assert "first second third" in out


def test_format_prefetch_falls_back_to_preview_field(bm):
    """BM may use 'preview' instead of 'content' in some response shapes."""
    payload = json.dumps(
        {
            "results": [
                {"title": "T", "permalink": "p/t", "preview": "preview-only"},
            ]
        }
    )
    out = _format(bm, payload)
    assert "preview-only" in out


def test_format_prefetch_handles_non_string_content(bm):
    """Defensive: BM could conceivably return non-string content fields."""
    payload = json.dumps(
        {
            "results": [
                {"title": "T", "permalink": "p/t", "content": 12345},
            ]
        }
    )
    # Must not raise
    out = _format(bm, payload)
    assert "12345" in out


def test_format_prefetch_handles_missing_title_permalink(bm):
    payload = json.dumps(
        {
            "results": [
                {"content": "orphan note"},
            ]
        }
    )
    out = _format(bm, payload)
    assert "(untitled)" in out
    assert "orphan note" in out


def test_format_prefetch_skips_non_dict_entries(bm):
    payload = json.dumps(
        {
            "results": [
                "not-a-dict",
                {"title": "Real", "permalink": "p/r", "content": "x"},
            ]
        }
    )
    out = _format(bm, payload)
    assert "Real" in out
    assert "not-a-dict" not in out


def test_format_prefetch_with_text_wrapped_results(bm):
    """BM text-format responses arrive wrapped as {"text": "..."} by _extract_mcp_text."""
    inner = json.dumps({"results": [{"title": "X", "permalink": "p/x", "content": "c"}]})
    payload = json.dumps({"text": inner})
    out = _format(bm, payload)
    assert "**X**" in out


def test_format_prefetch_empty(bm):
    assert _format(bm, json.dumps({"results": []})) == ""


def test_format_prefetch_no_results_key(bm):
    assert _format(bm, json.dumps({"foo": "bar"})) == ""


def test_format_prefetch_malformed(bm):
    assert _format(bm, "not-json") == ""


def test_format_prefetch_handles_extra_unknown_fields(bm):
    """Forward-compatibility: unknown fields shouldn't break formatting."""
    payload = json.dumps(
        {
            "results": [
                {
                    "title": "T",
                    "permalink": "p/t",
                    "content": "c",
                    "future_field_42": {"nested": "value"},
                    "score": 0.9,
                },
            ]
        }
    )
    out = _format(bm, payload)
    assert "**T**" in out
