"""Unit tests for the deterministic projector: dedup, replay-safety, mapping gate."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

from basic_memory.hooks import inbox
from basic_memory.hooks.envelope import (
    COMPACTION_IMMINENT,
    SESSION_STARTED,
    create_envelope,
)
from basic_memory.hooks.projector import flush, split_project_ref

WRITE_OK = {"title": "x", "action": "created"}


def _capture(
    session_id: str = "s-1",
    event: str = SESSION_STARTED,
    project_hint: str = "demo",
    ts: str = "2026-07-15T10:00:00+00:00",
    source: str = "claude-code",
    payload: dict | None = None,
) -> Path:
    envelope = create_envelope(
        source=source,
        event=event,
        session_id=session_id,
        cwd="/tmp/workdir",
        project_hint=project_hint,
        ts=ts,
        payload=payload or {},
    )
    return inbox.write_envelope(envelope)


def test_split_project_ref_routes_uuids_via_project_id() -> None:
    assert split_project_ref("0198f2b4-77aa-7bbf-9c2d-51e60a92d3c4") == (
        None,
        "0198f2b4-77aa-7bbf-9c2d-51e60a92d3c4",
    )
    assert split_project_ref("my-team/notes") == ("my-team/notes", None)


async def test_flush_projects_session_and_ledger(bm_home: Path) -> None:
    _capture(event=SESSION_STARTED, ts="2026-07-15T10:00:00+00:00")
    _capture(event=COMPACTION_IMMINENT, ts="2026-07-15T10:05:00+00:00")
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    assert result.swept == 2
    assert result.projected == 2
    assert result.pending == 0
    assert result.notes == ["Session s-1 (claude-code)", "Tool Ledger s-1 (claude-code)"]
    assert inbox.list_envelopes() == []
    assert len(list(inbox.processed_dir().glob("*.json"))) == 2
    assert inbox.last_flush() is not None

    session_call = mock_write.await_args_list[0]
    assert session_call.kwargs["project"] == "demo"
    assert session_call.kwargs["overwrite"] is True
    assert session_call.kwargs["directory"] == "sessions"
    content = session_call.kwargs["content"]
    assert "created_by: bm-hook/claude-code" in content
    assert "caused_by_event:" in content
    assert "- [source] claude-code/s-1" in content
    assert "session_started at 2026-07-15T10:00:00+00:00" in content
    assert "compaction_imminent at 2026-07-15T10:05:00+00:00" in content

    ledger_call = mock_write.await_args_list[1]
    ledger_content = ledger_call.kwargs["content"]
    assert "type: tool_ledger" in ledger_content
    assert "- [event] session_started at" in ledger_content
    assert "- [source] claude-code/s-1" in ledger_content


async def test_flush_uses_capture_folder_from_payload(bm_home: Path) -> None:
    _capture(payload={"capture_folder": "codex-sessions"})
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        await flush()

    assert mock_write.await_args_list[0].kwargs["directory"] == "codex-sessions"


async def test_flush_routes_uuid_project_hints_via_project_id(bm_home: Path) -> None:
    _capture(project_hint="0198f2b4-77aa-7bbf-9c2d-51e60a92d3c4")
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        await flush()

    assert (
        mock_write.await_args_list[0].kwargs["project_id"] == "0198f2b4-77aa-7bbf-9c2d-51e60a92d3c4"
    )


async def test_flush_dedups_idempotency_replays_within_a_sweep(bm_home: Path) -> None:
    # Same source/session/event/minute -> same idempotency key.
    _capture(ts="2026-07-15T10:00:01+00:00")
    _capture(ts="2026-07-15T10:00:41+00:00")
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    assert result.projected == 1
    assert result.duplicates == 1
    # The duplicate is retired, not re-projected: one session + one ledger write.
    assert mock_write.await_count == 2


async def test_flush_is_replay_safe_across_runs(bm_home: Path) -> None:
    _capture(ts="2026-07-15T10:00:01+00:00")
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        first = await flush()
        # The same hook replays after the first flush (same key, new envelope).
        _capture(ts="2026-07-15T10:00:59+00:00")
        second = await flush()

    assert first.projected == 1
    assert second.projected == 0
    assert second.duplicates == 1
    assert mock_write.await_count == 2  # no double-write for the replay


async def test_flush_leaves_unmapped_envelopes_pending(bm_home: Path) -> None:
    path = _capture(project_hint="")
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    assert result.pending == 1
    assert result.projected == 0
    mock_write.assert_not_awaited()
    assert inbox.list_envelopes() == [path]  # still pending, self-heals later


async def test_flush_leaves_group_pending_when_write_fails(bm_home: Path) -> None:
    path = _capture()
    mock_write = AsyncMock(side_effect=RuntimeError("api down"))

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    assert result.pending == 1
    assert result.projected == 0
    assert inbox.list_envelopes() == [path]


async def test_flush_leaves_group_pending_on_error_result(bm_home: Path) -> None:
    path = _capture()
    mock_write = AsyncMock(return_value={"error": "NOTE_WRITE_BLOCKED"})

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    assert result.pending == 1
    assert inbox.list_envelopes() == [path]


async def test_flush_counts_invalid_envelopes_and_leaves_them(bm_home: Path) -> None:
    valid = _capture()
    broken = valid.parent / f"{'0' * 8}-0000-7000-8000-{'0' * 12}.json"
    broken.write_text("{not json", encoding="utf-8")
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    assert result.invalid == 1
    assert result.projected == 1
    assert broken.exists()  # never deleted, never guessed at


async def test_flush_groups_sessions_independently(bm_home: Path) -> None:
    _capture(session_id="s-1")
    _capture(session_id="s-2", source="codex")
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    assert result.projected == 2
    titles = sorted(result.notes)
    assert "Session s-1 (claude-code)" in titles
    assert "Session s-2 (codex)" in titles
    assert mock_write.await_count == 4  # two artifacts per session group


def _plant_processed_with_age(days_old: int) -> Path:
    """Plant a processed envelope whose uuid7 filename encodes a capture age."""
    import uuid
    from datetime import datetime, timedelta, timezone

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


async def test_flush_prunes_expired_processed_envelopes(bm_home: Path) -> None:
    _plant_processed_with_age(days_old=45)
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    assert result.pruned == 1


async def test_flush_ignores_unreadable_processed_files_for_dedup(bm_home: Path) -> None:
    directory = inbox.processed_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "junk.json").write_text("{not json", encoding="utf-8")
    _capture()
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    assert result.projected == 1
