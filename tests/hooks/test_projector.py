"""Unit tests for the deterministic projector: dedup, replay-safety, mapping gate."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from basic_memory.hooks import inbox
from basic_memory.hooks.envelope import (
    COMPACTION_IMMINENT,
    SESSION_STARTED,
    create_envelope,
)
from basic_memory.hooks.projector import _artifact_username, flush, split_project_ref

WRITE_OK = {"title": "x", "action": "created"}


def _capture(
    session_id: str = "s-1",
    event: str = SESSION_STARTED,
    project_hint: str = "demo",
    ts: str = "2026-07-15T10:00:00+00:00",
    source: str = "claude-code",
    payload: dict | None = None,
    turn_id: str | None = None,
) -> Path:
    envelope = create_envelope(
        source=source,
        event=event,
        session_id=session_id,
        cwd="/tmp/workdir",
        project_hint=project_hint,
        ts=ts,
        turn_id=turn_id,
        payload=payload or {},
    )
    return inbox.write_envelope(envelope)


def test_split_project_ref_routes_uuids_via_project_id() -> None:
    assert split_project_ref("0198f2b4-77aa-7bbf-9c2d-51e60a92d3c4") == (
        None,
        "0198f2b4-77aa-7bbf-9c2d-51e60a92d3c4",
    )
    assert split_project_ref("my-team/notes") == ("my-team/notes", None)


def test_artifact_username_falls_back_to_environment() -> None:
    with (
        patch("basic_memory.hooks.projector.getuser", side_effect=OSError),
        patch.dict("os.environ", {"USER": "ci-user"}, clear=True),
    ):
        assert _artifact_username() == "ci-user"


def test_artifact_username_is_stable_when_unavailable() -> None:
    with (
        patch("basic_memory.hooks.projector.getuser", side_effect=KeyError),
        patch.dict("os.environ", {}, clear=True),
    ):
        assert _artifact_username() == "unknown"


async def test_flush_projects_session_and_ledger(bm_home: Path) -> None:
    _capture(event=SESSION_STARTED, ts="2026-07-15T10:00:00+00:00")
    _capture(event=COMPACTION_IMMINENT, ts="2026-07-15T10:05:00+00:00")
    mock_write = AsyncMock(return_value=WRITE_OK)

    with (
        patch("basic_memory.mcp.tools.write_note", mock_write),
        patch("basic_memory.hooks.projector.getuser", return_value="alice"),
        patch("basic_memory.hooks.projector.gethostname", return_value="devbox"),
    ):
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
    # Explicit note_type is what persists (content frontmatter type is stripped);
    # without it the artifact lands as `note` and session recall can't find it.
    assert session_call.kwargs["note_type"] == "session"
    # Provenance rides metadata= so write_note serializes it safely (never a
    # hand-built frontmatter block that an opaque field could invalidate).
    session_metadata = session_call.kwargs["metadata"]
    assert session_metadata["created_by"] == "bm-hook/claude-code"
    assert session_metadata["caused_by_event"]
    assert session_metadata["username"] == "alice"
    assert session_metadata["hostname"] == "devbox"
    assert session_metadata["status"] == "open"
    content = session_call.kwargs["content"]
    assert "- [source] claude-code/s-1" in content
    assert "session_started at 2026-07-15T10:00:00+00:00" in content
    assert "compaction_imminent at 2026-07-15T10:05:00+00:00" in content

    ledger_call = mock_write.await_args_list[1]
    ledger_content = ledger_call.kwargs["content"]
    assert ledger_call.kwargs["note_type"] == "tool_ledger"
    assert ledger_call.kwargs["metadata"]["created_by"] == "bm-hook/claude-code"
    assert ledger_call.kwargs["metadata"]["username"] == "alice"
    assert ledger_call.kwargs["metadata"]["hostname"] == "devbox"
    assert "- [event] session_started at" in ledger_content
    assert "- [source] claude-code/s-1" in ledger_content


async def test_flush_passes_yaml_special_turn_id_through_metadata(bm_home: Path) -> None:
    # source_turn_id is opaque, surface-defined text. A value with YAML-special
    # content must reach write_note as a structured metadata value (serialized
    # safely there), never hand-built into a `---` block that it would break —
    # which would wedge the session pending on every flush retry.
    _capture(event=SESSION_STARTED, turn_id="turn: 42\ninjected: true")
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    # One envelope retired (projected counts envelopes); it wrote both artifacts.
    assert result.projected == 1
    assert result.pending == 0
    session_call = mock_write.await_args_list[0]
    assert session_call.kwargs["metadata"]["envelope_turn_id"] == "turn: 42\ninjected: true"
    # The raw value is not spliced into a frontmatter block in the body.
    assert "---" not in session_call.kwargs["content"]


async def test_flush_collapses_control_characters_in_note_bodies(bm_home: Path) -> None:
    # Session ids are opaque, surface-defined text that skips the payload
    # redaction floor. A newline-carrying id must not break out of its markdown
    # line in EITHER artifact — titles, Events/Entries lines, and the ledger's
    # [source] observation would otherwise parse as injected observations or
    # relations in the projected note.
    _capture(session_id="s-1\n- [decision] injected\n- relates_to [[Evil]]")
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    assert result.projected == 1
    for call in mock_write.await_args_list:
        content = call.kwargs["content"]
        title = call.kwargs["title"]
        assert "\n- [decision] injected" not in content
        assert "\n- relates_to [[Evil]]" not in content
        assert "\n" not in title


async def test_flush_skips_when_another_flush_holds_the_lock(bm_home: Path) -> None:
    # Concurrent flushes race: a stale sweep can overwrite an artifact without a
    # sibling's just-retired event. Holding the inbox lock, a second flush must
    # skip entirely (write nothing, leave envelopes pending) rather than race.
    _capture(event=SESSION_STARTED)
    mock_write = AsyncMock(return_value=WRITE_OK)

    with inbox.flush_lock() as acquired:
        assert acquired is True
        with patch("basic_memory.mcp.tools.write_note", mock_write):
            result = await flush()

    assert result.skipped is True
    assert result.swept == 0
    assert mock_write.await_count == 0
    # The envelope is untouched — the next unlocked flush still projects it.
    assert len(inbox.list_envelopes()) == 1


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


async def test_flush_routes_on_project_hint_from_in_group_replay(bm_home: Path) -> None:
    # Same-minute same-event replay where the mapping was set only for the later
    # capture: the first envelope has no project_hint, the replay carries "demo".
    # Routing must use the replay's hint, not leave the group pending (and prune).
    _capture(event=SESSION_STARTED, ts="2026-07-15T10:00:01+00:00", project_hint="")
    _capture(event=SESSION_STARTED, ts="2026-07-15T10:00:41+00:00", project_hint="demo")
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    assert result.projected == 1
    assert result.pending == 0
    assert result.duplicates == 1
    assert mock_write.await_args_list[0].kwargs["project"] == "demo"
    assert inbox.list_envelopes() == []


async def test_flush_routes_new_event_via_processed_replay_hint(bm_home: Path) -> None:
    # Sweep 1 retires a no-hint original AND its hinted same-key replay into
    # processed/. A later distinct event for the same session must still route:
    # _dedup_by_key keeps the earlier (hint-less) processed envelope for the
    # rendered rows, so routing must scan the full processed history (which holds
    # the replay's hint), or the new event stays pending forever.
    mock_write = AsyncMock(return_value=WRITE_OK)
    with patch("basic_memory.mcp.tools.write_note", mock_write):
        _capture(event=SESSION_STARTED, ts="2026-07-15T10:00:01+00:00", project_hint="")
        _capture(event=SESSION_STARTED, ts="2026-07-15T10:00:41+00:00", project_hint="demo")
        first = await flush()
        _capture(event=COMPACTION_IMMINENT, ts="2026-07-15T10:05:00+00:00", project_hint="")
        second = await flush()

    assert first.projected == 1
    assert second.projected == 1  # routed via the processed replay's hint
    assert second.pending == 0
    assert inbox.list_envelopes() == []


async def test_flush_dedups_retired_replay_history_on_rebuild(bm_home: Path) -> None:
    # A same-minute SessionStart replay is retired into processed/ alongside its
    # original (same key, distinct id). When a later event triggers a full
    # rebuild, the replay must not resurface as a duplicate row.
    _capture(event=SESSION_STARTED, ts="2026-07-15T10:00:01+00:00")
    _capture(event=SESSION_STARTED, ts="2026-07-15T10:00:41+00:00")  # replay, same key
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        first = await flush()
        _capture(event=COMPACTION_IMMINENT, ts="2026-07-15T10:05:00+00:00")
        second = await flush()

    assert first.projected == 1
    assert first.duplicates == 1
    assert second.projected == 1

    session_writes = [
        call for call in mock_write.await_args_list if call.kwargs["title"].startswith("Session ")
    ]
    rebuilt = session_writes[-1].kwargs["content"]
    # Exactly one session_started event row (the "- <event> at" Events line),
    # despite the retired replay sitting in processed/.
    assert rebuilt.count("- session_started at") == 1
    assert "- compaction_imminent at" in rebuilt


async def test_flush_reprojects_after_write_failure_with_in_group_replay(bm_home: Path) -> None:
    # Two same-minute captures share one idempotency key: one fresh, one in-group
    # replay. If the write fails, retiring the replay early would let the next
    # sweep read its key from processed/ and wrongly retire the still-unprojected
    # fresh envelope — the session would never land. Both must stay pending and
    # project together on the healed sweep.
    _capture(ts="2026-07-15T10:00:01+00:00")
    _capture(ts="2026-07-15T10:00:41+00:00")  # same key (minute granularity)

    failing = AsyncMock(side_effect=RuntimeError("api down"))
    with patch("basic_memory.mcp.tools.write_note", failing):
        first = await flush()

    assert first.projected == 0
    assert first.pending == 1
    # Nothing retired — the in-group replay must not have moved to processed/.
    assert list(inbox.processed_dir().glob("*.json")) == []
    assert len(inbox.list_envelopes()) == 2

    healthy = AsyncMock(return_value=WRITE_OK)
    with patch("basic_memory.mcp.tools.write_note", healthy):
        second = await flush()

    assert second.projected == 1
    assert second.duplicates == 1
    assert healthy.await_count == 2  # one session + one ledger, no double-write
    assert inbox.list_envelopes() == []


async def test_flush_leaves_group_pending_on_error_result(bm_home: Path) -> None:
    path = _capture()
    mock_write = AsyncMock(return_value={"error": "NOTE_WRITE_BLOCKED"})

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    assert result.pending == 1
    assert inbox.list_envelopes() == [path]


async def test_flush_skips_vanished_inbox_file(bm_home: Path) -> None:
    # A pending file that disappears (a concurrent flush retired it) or is
    # transiently unreadable between listing and reading must not abort the
    # sweep: the valid envelope still projects and the flush marker is recorded.
    good = _capture()
    ghost = inbox.inbox_dir() / "gone.json"  # listed but never on disk
    mock_write = AsyncMock(return_value=WRITE_OK)

    with (
        patch("basic_memory.mcp.tools.write_note", mock_write),
        patch.object(inbox, "list_envelopes", return_value=[good, ghost]),
    ):
        result = await flush()

    assert result.projected == 1
    assert result.swept == 2
    assert result.invalid == 0  # a vanished file is skipped, not counted corrupt
    assert inbox.last_flush() is not None


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


async def test_flush_counts_type_corrupt_envelope_and_keeps_sweeping(bm_home: Path) -> None:
    # Valid JSON, right keys, wrong scalar type (session id is a list). Must be
    # counted invalid — not crash the sweep grouping on an unhashable id — so the
    # valid envelope still projects.
    valid = _capture()
    corrupt = valid.parent / f"{'0' * 8}-0000-7000-8000-{'0' * 12}.json"
    payload = json.loads(valid.read_text(encoding="utf-8"))
    payload["source_session_id"] = []
    corrupt.write_text(json.dumps(payload), encoding="utf-8")
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    assert result.invalid == 1
    assert result.projected == 1
    assert corrupt.exists()  # left in place for inspection


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


def _uuid7_aged(days_old: int):
    """A uuid7 whose embedded timestamp is ``days_old`` days in the past."""
    import uuid
    from datetime import datetime, timedelta, timezone

    captured = datetime.now(timezone.utc) - timedelta(days=days_old)
    captured_ms = int(captured.timestamp() * 1000)
    value = (captured_ms & 0xFFFF_FFFF_FFFF) << 80
    value |= 0x7 << 76
    value |= 0b10 << 62
    return uuid.UUID(int=value)


def _plant_processed_with_age(days_old: int) -> Path:
    """Plant a processed envelope whose uuid7 filename encodes a capture age."""
    directory = inbox.processed_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_uuid7_aged(days_old)}.json"
    path.write_text("{}", encoding="utf-8")
    return path


def _plant_pending_with_age(
    days_old: int,
    project_hint: str = "",
    *,
    event: str = SESSION_STARTED,
    session_id: str = "stale",
    ts: str = "2026-07-15T10:00:00+00:00",
) -> Path:
    """Plant a pending inbox envelope with a valid body and an aged filename."""
    from basic_memory.hooks.envelope import envelope_to_json

    envelope = create_envelope(
        source="claude-code",
        event=event,
        session_id=session_id,
        cwd="/tmp/workdir",
        project_hint=project_hint,
        ts=ts,
    )
    directory = inbox.inbox_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_uuid7_aged(days_old)}.json"
    path.write_text(envelope_to_json(envelope), encoding="utf-8")
    return path


async def test_flush_prunes_expired_processed_envelopes(bm_home: Path) -> None:
    _plant_processed_with_age(days_old=45)
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    assert result.pruned == 1


async def test_flush_prunes_expired_unresolved_pending_envelopes(bm_home: Path) -> None:
    # A fully-unmapped session (no project_hint ever) would otherwise sit pending
    # forever; retention bounds the inbox the same way it bounds processed/.
    stale = _plant_pending_with_age(days_old=45)
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    assert result.pruned == 1
    assert not stale.exists()
    mock_write.assert_not_awaited()


async def test_flush_prune_skips_non_file_glob_matches(bm_home: Path) -> None:
    # A directory whose name happens to match *.json must be skipped by
    # retention, not unlinked (which would crash the sweep).
    stray = inbox.inbox_dir() / f"{_uuid7_aged(45)}.json"
    stray.mkdir(parents=True)
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    assert stray.is_dir()  # never unlinked
    assert result.pruned == 0


async def test_flush_keeps_recent_unmapped_pending_envelopes(bm_home: Path) -> None:
    # Within the window, unmapped trace is kept — a mapping may still resolve it.
    fresh = _plant_pending_with_age(days_old=1)
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    assert result.pruned == 0
    assert fresh.exists()


async def test_flush_keeps_hintless_envelope_when_session_is_routable(bm_home: Path) -> None:
    # e1 captured before primaryProject was set (hint-less), e2 after (hinted),
    # same session, both past retention, and the write still fails (outage). The
    # session is routable via e2, so retention must keep BOTH — pruning the
    # hint-less e1 would drop its event from the session the next successful
    # sweep rebuilds.
    e1 = _plant_pending_with_age(
        days_old=46, project_hint="", event=SESSION_STARTED, session_id="s-mixed"
    )
    e2 = _plant_pending_with_age(
        days_old=45,
        project_hint="demo",
        event=COMPACTION_IMMINENT,
        session_id="s-mixed",
        ts="2026-07-15T10:05:00+00:00",
    )
    failing = AsyncMock(side_effect=RuntimeError("cloud down"))

    with patch("basic_memory.mcp.tools.write_note", failing):
        result = await flush()

    assert e1.exists()  # hint-less, but its session is routable via e2
    assert e2.exists()
    assert result.pruned == 0


async def test_flush_keeps_write_failed_mapped_envelope_past_retention(bm_home: Path) -> None:
    # An old pending envelope WITH a project hint is pending because a write
    # failed (cloud/API outage), not because it's unmappable. Retention must not
    # drop it — that would defeat the self-heal path; only unresolvable
    # (hint-less) trace is pruned.
    old = _plant_pending_with_age(days_old=45, project_hint="demo")
    failing = AsyncMock(side_effect=RuntimeError("cloud down"))

    with patch("basic_memory.mcp.tools.write_note", failing):
        result = await flush()

    assert old.exists()  # kept despite being past retention
    assert result.pruned == 0
    assert result.pending == 1


async def test_flush_ignores_unreadable_processed_files_for_dedup(bm_home: Path) -> None:
    directory = inbox.processed_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "junk.json").write_text("{not json", encoding="utf-8")
    _capture()
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    assert result.projected == 1


async def test_flush_preserves_prior_events_on_incremental_flush(bm_home: Path) -> None:
    """Regression: a later sweep must not erase events an earlier sweep projected.

    session_started is projected and retired to processed/ on flush 1; when
    compaction_imminent arrives, flush 2 must rebuild the note from the full
    session, not just the pending envelope. Overwriting from fresh alone would
    drop session_started.
    """
    mock_write = AsyncMock(return_value=WRITE_OK)
    with patch("basic_memory.mcp.tools.write_note", mock_write):
        _capture(event=SESSION_STARTED, ts="2026-07-15T10:00:00+00:00")
        await flush()
        _capture(event=COMPACTION_IMMINENT, ts="2026-07-15T10:30:00+00:00")
        await flush()

    last_session = [
        call for call in mock_write.await_args_list if call.kwargs["title"].startswith("Session ")
    ][-1]
    content = last_session.kwargs["content"]
    assert "session_started at 2026-07-15T10:00:00+00:00" in content
    assert "compaction_imminent at 2026-07-15T10:30:00+00:00" in content


async def test_flush_distinguishes_sessions_sharing_a_prefix(bm_home: Path) -> None:
    """Regression: sessions sharing an 8-char prefix get distinct titles/permalinks."""
    _capture(session_id="abcd1234-session-one")
    _capture(session_id="abcd1234-session-two")
    mock_write = AsyncMock(return_value=WRITE_OK)

    with patch("basic_memory.mcp.tools.write_note", mock_write):
        result = await flush()

    session_titles = {title for title in result.notes if title.startswith("Session ")}
    assert session_titles == {
        "Session abcd1234-session-one (claude-code)",
        "Session abcd1234-session-two (claude-code)",
    }
