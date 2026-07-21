"""Tests for the local lifecycle-trace archive sweep."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from basic_memory.hooks import inbox
from basic_memory.hooks.envelope import SESSION_STARTED, create_envelope
from basic_memory.hooks.archive import flush
from basic_memory.hooks.project_ref import split_project_ref


def _capture(
    *,
    session_id: str = "s-1",
    project_hint: str = "demo",
    ts: str = "2026-07-15T10:00:00+00:00",
) -> Path:
    return inbox.write_envelope(
        create_envelope(
            source="codex",
            event=SESSION_STARTED,
            session_id=session_id,
            cwd="/tmp/workdir",
            project_hint=project_hint,
            ts=ts,
        )
    )


def test_split_project_ref_routes_uuids_via_project_id() -> None:
    assert split_project_ref("0198f2b4-77aa-7bbf-9c2d-51e60a92d3c4") == (
        None,
        "0198f2b4-77aa-7bbf-9c2d-51e60a92d3c4",
    )
    assert split_project_ref("my-team/notes") == ("my-team/notes", None)


async def test_flush_archives_trace_without_writing_graph_notes(bm_home: Path) -> None:
    _capture(project_hint="")
    _capture(session_id="s-2", project_hint="demo", ts="2026-07-15T10:01:00+00:00")
    mock_write = AsyncMock()

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    assert result.swept == 2
    assert result.archived == 2
    assert result.pending == 0
    assert result.invalid == 0
    mock_write.assert_not_awaited()
    assert inbox.list_envelopes() == []
    assert len(list(inbox.processed_dir().glob("*.json"))) == 2
    assert inbox.last_flush() is not None


async def test_flush_deduplicates_replays_within_and_across_sweeps(bm_home: Path) -> None:
    _capture(ts="2026-07-15T10:00:01+00:00")
    _capture(ts="2026-07-15T10:00:41+00:00")

    first = await flush()
    _capture(ts="2026-07-15T10:00:59+00:00")
    second = await flush()

    assert first.archived == 1
    assert first.duplicates == 1
    assert second.archived == 0
    assert second.duplicates == 1
    assert len(list(inbox.processed_dir().glob("*.json"))) == 3


async def test_flush_counts_invalid_envelopes_and_leaves_them(bm_home: Path) -> None:
    valid = _capture()
    broken = valid.parent / f"{'0' * 8}-0000-7000-8000-{'0' * 12}.json"
    broken.write_text("{not json", encoding="utf-8")

    result = await flush()

    assert result.archived == 1
    assert result.invalid == 1
    assert broken.exists()


async def test_flush_counts_type_corrupt_envelope_and_keeps_sweeping(bm_home: Path) -> None:
    valid = _capture()
    corrupt = valid.parent / f"{'1' * 8}-0000-7000-8000-{'1' * 12}.json"
    payload = json.loads(valid.read_text(encoding="utf-8"))
    payload["source_session_id"] = []
    corrupt.write_text(json.dumps(payload), encoding="utf-8")

    result = await flush()

    assert result.archived == 1
    assert result.invalid == 1
    assert corrupt.exists()


async def test_flush_leaves_envelope_pending_when_local_archive_fails(bm_home: Path) -> None:
    path = _capture()

    with patch.object(inbox, "mark_processed", side_effect=OSError("disk full")):
        result = await flush()

    assert result.archived == 0
    assert result.pending == 1
    assert inbox.list_envelopes() == [path]


async def test_flush_skips_when_another_flush_holds_the_lock(bm_home: Path) -> None:
    _capture()

    with inbox.flush_lock() as acquired:
        assert acquired is True
        result = await flush()

    assert result.skipped is True
    assert result.swept == 0
    assert len(inbox.list_envelopes()) == 1


async def test_flush_skips_vanished_inbox_file(bm_home: Path) -> None:
    good = _capture()
    ghost = inbox.inbox_dir() / "gone.json"

    with patch.object(inbox, "list_envelopes", return_value=[good, ghost]):
        result = await flush()

    assert result.archived == 1
    assert result.swept == 2
    assert result.invalid == 0


def _uuid7_aged(days_old: int):
    import uuid
    from datetime import datetime, timedelta, timezone

    captured = datetime.now(timezone.utc) - timedelta(days=days_old)
    captured_ms = int(captured.timestamp() * 1000)
    value = (captured_ms & 0xFFFF_FFFF_FFFF) << 80
    value |= 0x7 << 76
    value |= 0b10 << 62
    return uuid.UUID(int=value)


async def test_flush_prunes_only_expired_archived_trace(bm_home: Path) -> None:
    directory = inbox.processed_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stale = directory / f"{_uuid7_aged(45)}.json"
    stale.write_text("{}", encoding="utf-8")
    pending = _capture(project_hint="")

    result = await flush(older_than_days=30)

    assert result.pruned == 1
    assert not stale.exists()
    # Project mapping is irrelevant now: valid pending trace is archived, not pruned.
    assert not pending.exists()
