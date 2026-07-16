"""Unit tests for the inbox WAL: atomicity, ordering, retention."""

import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from basic_memory.hooks import _uuid7, inbox
from basic_memory.hooks.envelope import SESSION_STARTED, create_envelope


def _envelope(session_id: str = "s-1"):
    return create_envelope(
        source="claude-code",
        event=SESSION_STARTED,
        session_id=session_id,
        cwd="/tmp/workdir",
        project_hint="demo",
    )


def test_write_envelope_is_atomic_and_named_by_id(bm_home: Path) -> None:
    envelope = _envelope()

    path = inbox.write_envelope(envelope)

    assert path == bm_home / "inbox" / f"{envelope.id}.json"
    assert path.is_file()
    # tmp + rename leaves no stragglers behind
    assert list(path.parent.glob("*.tmp")) == []


def test_list_envelopes_sorts_by_filename(bm_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base_ns = time.time_ns()
    paths = []
    # Write out of wall-clock order to prove the listing sorts by name alone.
    for offset_ms in (5, 1, 9):
        monkeypatch.setattr(_uuid7.time, "time_ns", lambda ns=base_ns + offset_ms * 1_000_000: ns)
        paths.append(inbox.write_envelope(_envelope()))

    listed = inbox.list_envelopes()

    assert listed == sorted(paths)


def test_list_envelopes_ignores_processed_and_non_json(bm_home: Path) -> None:
    kept = inbox.write_envelope(_envelope())
    inbox.mark_processed(inbox.write_envelope(_envelope(session_id="s-2")))
    (bm_home / "inbox" / "notes.txt").write_text("not an envelope", encoding="utf-8")

    assert inbox.list_envelopes() == [kept]


def test_list_envelopes_handles_missing_inbox(bm_home: Path) -> None:
    assert inbox.list_envelopes() == []


def test_mark_processed_moves_into_processed_dir(bm_home: Path) -> None:
    path = inbox.write_envelope(_envelope())

    destination = inbox.mark_processed(path)

    assert not path.exists()
    assert destination == bm_home / "inbox" / "processed" / path.name
    assert destination.is_file()


def _write_processed_with_age(days_old: int) -> Path:
    """Plant a processed envelope whose uuid7 filename encodes a capture age."""
    captured = datetime.now(timezone.utc) - timedelta(days=days_old)
    captured_ms = int(captured.timestamp() * 1000)
    value = (captured_ms & 0xFFFF_FFFF_FFFF) << 80
    value |= 0x7 << 76
    value |= 0b10 << 62
    file_id = uuid.UUID(int=value)
    directory = inbox.processed_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{file_id}.json"
    path.write_text("{}", encoding="utf-8")
    return path


def test_prune_processed_removes_only_expired_envelopes(bm_home: Path) -> None:
    old = _write_processed_with_age(days_old=45)
    fresh = _write_processed_with_age(days_old=5)

    removed = inbox.prune_processed(older_than_days=30)

    assert removed == 1
    assert not old.exists()
    assert fresh.exists()


def test_prune_processed_never_deletes_non_uuid_files(bm_home: Path) -> None:
    directory = inbox.processed_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stray = directory / "not-a-uuid.json"
    stray.write_text("{}", encoding="utf-8")

    assert inbox.prune_processed(older_than_days=0) == 0
    assert stray.exists()


def test_prune_processed_never_deletes_non_v7_uuid_files(bm_home: Path) -> None:
    """A parseable but non-v7 UUID name has no capture time — never age it out.

    A blind 80-bit shift of a v4 UUID yields a garbage timestamp that could
    fall before any cutoff; retention must not act on data it can't date.
    """
    directory = inbox.processed_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stray = directory / f"{uuid.uuid4()}.json"
    stray.write_text("{}", encoding="utf-8")

    assert inbox.prune_processed(older_than_days=0) == 0
    assert stray.exists()


def test_mark_processed_tolerates_already_retired_envelope(bm_home: Path) -> None:
    """Regression: a concurrent sweep that already moved the envelope must not crash.

    Two flushes can retire the same envelope; the loser sees a missing source but
    the destination already present, and returns it instead of aborting midway.
    """
    path = inbox.write_envelope(_envelope())

    first = inbox.mark_processed(path)
    second = inbox.mark_processed(path)

    assert first == second
    assert second.exists()


def test_mark_processed_reraises_when_source_and_dest_missing(bm_home: Path) -> None:
    """A genuinely missing envelope (no source, no destination) is a real error."""
    inbox.inbox_dir().mkdir(parents=True, exist_ok=True)
    missing = inbox.inbox_dir() / f"{uuid.uuid4()}.json"

    with pytest.raises(FileNotFoundError):
        inbox.mark_processed(missing)


def test_prune_processed_handles_missing_dir(bm_home: Path) -> None:
    assert inbox.prune_processed() == 0


def test_flush_marker_roundtrip(bm_home: Path) -> None:
    assert inbox.last_flush() is None

    inbox.record_flush(ts="2026-07-15T10:00:00+00:00")

    assert inbox.last_flush() == "2026-07-15T10:00:00+00:00"


def test_record_flush_defaults_to_now(bm_home: Path) -> None:
    inbox.record_flush()

    stamp = inbox.last_flush()
    assert stamp is not None and stamp.startswith("20")
