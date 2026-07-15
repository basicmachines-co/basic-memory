"""Adapter tests against recorded harness hook payload fixtures."""

import json
from pathlib import Path

import pytest

from basic_memory.hooks.adapters import for_harness
from basic_memory.hooks.envelope import COMPACTION_IMMINENT, SESSION_STARTED

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


# --- Claude Code ---


def test_claude_session_start_fixture_normalizes() -> None:
    payload = load_fixture("claude_session_start.json")

    event = for_harness("claude").normalize(SESSION_STARTED, payload)

    assert event.source == "claude-code"
    assert event.event == SESSION_STARTED
    assert event.session_id == "3f9a2b1c-4d5e-6f70-8a9b-0c1d2e3f4a5b"
    assert event.cwd == "/Users/dev/projects/demo"
    assert event.transcript_path.endswith("3f9a2b1c.jsonl")
    assert event.trigger == "startup"  # SessionStart reports its cause as `source`
    assert event.turn_id is None
    assert event.model is None


def test_claude_pre_compact_fixture_normalizes() -> None:
    payload = load_fixture("claude_pre_compact.json")

    event = for_harness("claude").normalize(COMPACTION_IMMINENT, payload)

    assert event.event == COMPACTION_IMMINENT
    assert event.trigger == "auto"
    assert event.session_id == "3f9a2b1c-4d5e-6f70-8a9b-0c1d2e3f4a5b"


def test_claude_missing_fields_normalize_to_defaults() -> None:
    event = for_harness("claude").normalize(SESSION_STARTED, {})

    assert event.session_id == ""
    assert event.cwd == ""
    assert event.transcript_path == ""
    assert event.trigger is None


# --- Codex ---


def test_codex_session_start_fixture_normalizes() -> None:
    payload = load_fixture("codex_session_start.json")

    event = for_harness("codex").normalize(SESSION_STARTED, payload)

    assert event.source == "codex"
    assert event.session_id == "0198f2b4-77aa-7bbf-9c2d-51e60a92d3c4"
    assert event.trigger == "startup"
    assert event.turn_id is None
    assert event.model is None


def test_codex_pre_compact_fixture_normalizes() -> None:
    payload = load_fixture("codex_pre_compact.json")

    event = for_harness("codex").normalize(COMPACTION_IMMINENT, payload)

    assert event.turn_id == "turn-42"
    assert event.trigger == "auto"
    assert event.model == "gpt-5.2-codex"


def test_codex_missing_fields_normalize_to_defaults() -> None:
    event = for_harness("codex").normalize(COMPACTION_IMMINENT, {})

    assert event.session_id == ""
    assert event.turn_id is None
    assert event.model is None


# --- Registry ---


def test_for_harness_rejects_unknown_harness() -> None:
    with pytest.raises(ValueError, match="Unknown harness 'cursor'"):
        for_harness("cursor")
