"""Deterministic projector: sweep the inbox into knowledge-graph artifacts.

The interim consumer of the harness WAL (``bm hook flush``) until the SPEC-54
daemon worker lands. No LLM: SessionNote skeletons and ToolLedger entries are
derived mechanically from the captured envelopes.

Idempotent by construction (the EverOS pattern): envelopes are treated as
hints — dedup on ``idempotency_key``, artifacts re-derived with deterministic
titles and ``overwrite=True`` — so WAL replays and duplicate hooks can never
corrupt or double-write. Every run sweeps the whole inbox, so envelopes
captured while nothing was consuming self-heal; there is no missed-event
window.

Writes go through the same internal write path the CLI's ``write-note`` uses
(the MCP ``write_note`` tool via the async client) — never a subprocess.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from basic_memory.hooks import inbox
from basic_memory.hooks.envelope import (
    Envelope,
    SessionKey,
    envelope_from_json,
    to_frontmatter_fields,
    to_provenance_observations,
)

# Cloud project refs come in two unambiguous forms (names collide across
# workspaces): a workspace-qualified name routes via project, an external_id
# UUID via project_id. Mirrors the routing the hook scripts used.
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

DEFAULT_CAPTURE_FOLDER = "sessions"
CREATED_BY_PREFIX = "bm-hook"

# Artifact note types. These are the explicit `note_type` write_note persists —
# the `type:` in the rendered frontmatter is stripped and replaced by this arg,
# so both must agree or recall (search by type) can't find projected notes.
SESSION_NOTE_TYPE = "session"
TOOL_LEDGER_NOTE_TYPE = "tool_ledger"


@dataclass
class FlushResult:
    """What one projector sweep did, for `bm hook flush` / `status` reporting."""

    swept: int = 0  # envelope files seen in the inbox
    projected: int = 0  # envelopes promoted into artifacts
    duplicates: int = 0  # idempotency-key replays retired without writing
    pending: int = 0  # left in the inbox (no project mapping, or write failed)
    invalid: int = 0  # unreadable envelope files left in place
    pruned: int = 0  # envelopes removed by retention (processed + unresolved pending)
    notes: list[str] = field(default_factory=list)  # artifact titles written


def split_project_ref(ref: str) -> tuple[str | None, str | None]:
    """Split a project reference into the (project, project_id) routing pair.

    A UUID reference must route via ``project_id``, not ``project``, or the
    call silently fails to land in a UUID-configured project.
    """
    if UUID_RE.match(ref):
        return None, ref
    return ref, None


def _processed_envelopes_by_session() -> dict[SessionKey, list[Envelope]]:
    """Already-projected envelopes, grouped by session key.

    A session's artifacts are overwritten in full on every sweep, so re-deriving
    them from only the still-pending envelopes would drop everything projected on
    an earlier sweep (session_started vanishing once session_ended arrives).
    Reloading the processed envelopes lets each sweep rebuild the note from the
    complete session history (bounded by retention). Corrupt processed files are
    skipped, never deleted.
    """
    grouped: dict[SessionKey, list[Envelope]] = {}
    for path in inbox.processed_dir().glob("*.json"):
        try:
            envelope = envelope_from_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        grouped.setdefault(envelope.session_key, []).append(envelope)
    return grouped


def _session_label(session_id: str) -> str:
    # Full id, not a prefix: two sessions sharing an 8-char prefix would derive
    # the same title, the same permalink, and clobber each other's notes.
    return session_id or "unknown"


def _dedup_by_key(envelopes: list[Envelope]) -> list[Envelope]:
    """Keep one envelope per idempotency key, preserving input order.

    A retired replay lives in ``processed/`` next to its original (same key,
    distinct id), so a full rebuild that merges processed history would render
    both as duplicate rows. Deduping the merged list — earliest first, since the
    caller sorts by chronological uuid7 id — keeps the original and drops the
    replay, honoring the idempotency contract.
    """
    seen: set[str] = set()
    unique: list[Envelope] = []
    for envelope in envelopes:
        if envelope.idempotency_key in seen:
            continue
        seen.add(envelope.idempotency_key)
        unique.append(envelope)
    return unique


def _capture_folder(envelopes: list[Envelope]) -> str:
    # Capture embeds the harness's configured folder into the payload so the
    # projector needs no settings access of its own.
    for envelope in envelopes:
        folder = envelope.payload.get("capture_folder")
        if isinstance(folder, str) and folder.strip():
            return folder.strip()
    return DEFAULT_CAPTURE_FOLDER


def _artifact_frontmatter(note_type: str, first: Envelope) -> list[str]:
    """Common provenance frontmatter every projected artifact carries."""
    lines = [
        "---",
        f"type: {note_type}",
        f"created_by: {CREATED_BY_PREFIX}/{first.source}",
        f"caused_by_event: {first.id}",
    ]
    lines += [f"{key}: {value}" for key, value in to_frontmatter_fields(first).items()]
    lines.append("---")
    return lines


def _session_note(source: str, session_id: str, envelopes: list[Envelope]) -> tuple[str, str]:
    """Derive the SessionNote skeleton (title, content) for one session group."""
    first = envelopes[0]
    title = f"Session {_session_label(session_id)} ({source})"
    frontmatter = _artifact_frontmatter(SESSION_NOTE_TYPE, first)
    # status/open mirrors the checkpoint notes so structured recall finds both.
    frontmatter.insert(2, "status: open")

    body = [
        "",
        f"# {title}",
        "",
        "_Session skeleton projected from captured harness events by `bm hook flush`._",
        "",
        "## Events",
        *[f"- {envelope.event} at {envelope.ts} (`{envelope.id}`)" for envelope in envelopes],
        "",
        "## Observations",
        *to_provenance_observations(first),
    ]
    return title, "\n".join(frontmatter + body)


def _tool_ledger_note(source: str, session_id: str, envelopes: list[Envelope]) -> tuple[str, str]:
    """Derive the ToolLedger (title, content) for one session group.

    V0 captures only lifecycle events, so the ledger records those; tool_called
    entries join when PostToolUse capture lands.
    """
    first = envelopes[0]
    title = f"Tool Ledger {_session_label(session_id)} ({source})"
    frontmatter = _artifact_frontmatter(TOOL_LEDGER_NOTE_TYPE, first)

    entries = [
        f"- [event] {envelope.event} at {envelope.ts} "
        f"(actor: {envelope.actor}, idempotency: {envelope.idempotency_key})"
        for envelope in envelopes
    ]
    body = [
        "",
        f"# {title}",
        "",
        "_Event ledger projected from captured harness events by `bm hook flush`._",
        "",
        "## Entries",
        *entries,
        "",
        "## Observations",
        f"- [source] {source}/{session_id}",
    ]
    return title, "\n".join(frontmatter + body)


async def _write_artifact(
    title: str, content: str, folder: str, project_hint: str, note_type: str
) -> None:
    # Deferred: importing basic_memory.mcp.tools loads the whole tool stack
    # (fastmcp, SQLAlchemy) and must not happen at CLI import time (#886).
    from basic_memory.mcp.tools import write_note

    project, project_id = split_project_ref(project_hint)
    result = await write_note(
        title=title,
        content=content,
        directory=folder,
        project=project,
        project_id=project_id,
        tags=["auto-capture"],
        # Explicit note_type: write_note strips `type:` from the content
        # frontmatter and persists this arg instead. Without it the artifact
        # lands as the default `note`, invisible to session/ledger type recall.
        note_type=note_type,
        overwrite=True,
        output_format="json",
    )
    # write_note reports failures as an error field in JSON mode; surface it as
    # an exception so the group stays pending instead of being retired unwritten.
    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(f"write_note failed for {title!r}: {result['error']}")


async def flush(older_than_days: int = inbox.DEFAULT_RETENTION_DAYS) -> FlushResult:
    """Sweep the whole inbox and project it into artifacts.

    Envelopes without a resolvable project mapping stay pending — fail fast,
    never write to the wrong project. Groups whose write fails also stay
    pending and self-heal on the next sweep.
    """
    result = FlushResult()

    # --- Load the inbox in capture order ---
    entries: list[tuple[Path, Envelope]] = []
    for path in inbox.list_envelopes():
        result.swept += 1
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            # Trigger: the file vanished (a concurrent flush retired it) or is
            # transiently unreadable between listing and reading.
            # Why: one missing/locked file must never abort the whole sweep and
            #      skip the remaining valid envelopes + the flush marker.
            # Outcome: skip it — it isn't corrupt trace, just gone or busy; the
            #      sweep that owns it handles it. Not counted as invalid.
            logger.debug(f"skipping unreadable envelope {path.name}: {exc}")
            continue
        try:
            entries.append((path, envelope_from_json(text)))
        except (ValueError, json.JSONDecodeError) as exc:
            # Trigger: corrupt or future-versioned envelope file.
            # Why: deleting it would destroy trace; projecting it would guess.
            # Outcome: left in place, counted, visible in `bm hook status`.
            logger.warning(f"skipping invalid envelope {path.name}: {exc}")
            result.invalid += 1

    # --- Group by session, preserving capture order within each group ---
    groups: dict[SessionKey, list[tuple[Path, Envelope]]] = {}
    for path, envelope in entries:
        groups.setdefault(envelope.session_key, []).append((path, envelope))

    processed_by_session = _processed_envelopes_by_session()
    seen_keys = {
        envelope.idempotency_key
        for envelopes in processed_by_session.values()
        for envelope in envelopes
    }

    # Sessions with a hint *anywhere* (a pending envelope this sweep, or an
    # already-processed one) are routable: a hint-less pending envelope in such a
    # session self-heals via the merge, so retention must not prune it even when
    # it's past the window (e.g. captured before primaryProject was set, during a
    # prolonged write outage). Only fully-unmapped sessions are prunable.
    routable_sessions = frozenset(
        key
        for source_map in (
            {k: [e for _, e in v] for k, v in groups.items()},
            processed_by_session,
        )
        for key, envelopes in source_map.items()
        if any(envelope.project_hint.strip() for envelope in envelopes)
    )

    for session_key, group in groups.items():
        source, session_id = session_key
        # --- Dedup: envelopes are hints, never double-write ---
        # Two replay kinds, retired at different times: one duplicating an
        # already-projected envelope (durable in processed/ — safe to retire now)
        # and one duplicating an in-group sibling being projected this sweep
        # (safe only once that sibling's write succeeds).
        fresh: list[tuple[Path, Envelope]] = []
        processed_replays: list[Path] = []
        group_replays: list[tuple[Path, Envelope]] = []
        group_keys: set[str] = set()
        for path, envelope in group:
            if envelope.idempotency_key in seen_keys:
                processed_replays.append(path)
            elif envelope.idempotency_key in group_keys:
                group_replays.append((path, envelope))
            else:
                group_keys.add(envelope.idempotency_key)
                fresh.append((path, envelope))

        # A replay of an already-projected envelope is safe to retire immediately.
        for path in processed_replays:
            inbox.mark_processed(path)
            result.duplicates += 1

        # No fresh work means no in-group siblings to project, so group_replays is
        # empty here (a key's first occurrence is always the fresh one).
        if not fresh:
            continue

        fresh_envelopes = [envelope for _, envelope in fresh]
        # Rebuild from the COMPLETE session: previously-projected envelopes (now
        # in processed/) merged with the fresh ones in capture order (uuid7 ids
        # sort chronologically). Overwriting from fresh alone would erase events
        # projected on an earlier sweep. Dedup by idempotency key so a replay
        # that was retired into processed/ next to its original doesn't resurface
        # as a duplicate row here.
        prior = processed_by_session.get(session_key, [])
        replay_envelopes = [envelope for _, envelope in group_replays]
        # Routing scans the COMPLETE, UN-deduped history — prior processed
        # envelopes, fresh, and in-group replays alike. A mapping can land on the
        # later same-key capture (primaryProject set between two same-minute
        # hooks) while the first is unmapped, and _dedup_by_key keeps the earlier
        # (hint-less) one for the rendered rows — so scanning the deduped list
        # would miss a hint that only the dropped replay carries, leaving the
        # group pending forever (routable_sessions also spares it from pruning).
        project_hint = next(
            (
                envelope.project_hint.strip()
                for envelope in (*prior, *fresh_envelopes, *replay_envelopes)
                if envelope.project_hint.strip()
            ),
            "",
        )
        # Rendered rows still dedup by key so a retired replay in processed/
        # doesn't resurface as a duplicate row.
        envelopes = _dedup_by_key(sorted(prior + fresh_envelopes, key=lambda envelope: envelope.id))
        if not project_hint:
            # Trigger: no project mapping resolved for this session.
            # Why: writing to a default/guessed project would put trace in the
            #      wrong graph — the one unrecoverable failure mode.
            # Outcome: envelopes stay pending — a later same-session capture may
            #      carry a hint (resolving the whole group via the merge above),
            #      and retention prunes them if none ever does. Retire nothing,
            #      including group_replays: they must self-heal alongside fresh.
            result.pending += len(fresh)
            continue

        session_title, session_content = _session_note(source, session_id, envelopes)
        ledger_title, ledger_content = _tool_ledger_note(source, session_id, envelopes)
        folder = _capture_folder(envelopes)
        try:
            await _write_artifact(
                session_title, session_content, folder, project_hint, SESSION_NOTE_TYPE
            )
            await _write_artifact(
                ledger_title, ledger_content, folder, project_hint, TOOL_LEDGER_NOTE_TYPE
            )
        except Exception as exc:
            # Trigger: the write path failed (project missing, API error, ...).
            # Why: retiring unwritten envelopes would silently drop events.
            # Outcome: group stays pending; the next sweep re-derives it. Leave
            #      group_replays pending too — retiring them now would let the
            #      next sweep read their key from processed/ and wrongly retire
            #      the still-unwritten fresh envelope as a replay.
            logger.warning(f"flush left {source}/{session_id} pending: {exc}")
            result.pending += len(fresh)
            continue

        # Write succeeded: retire the fresh envelopes and only now the in-group
        # replays they duplicated.
        for path, _ in fresh:
            inbox.mark_processed(path)
            result.projected += 1
        for path, _ in group_replays:
            inbox.mark_processed(path)
            result.duplicates += 1
        result.notes += [session_title, ledger_title]

    # Retire both sides on the same window: processed audit copies, and pending
    # trace from fully-unmapped sessions (so the inbox can't grow without limit).
    # routable_sessions are spared — a hint-less file whose session is routable
    # self-heals, so pruning it would drop events from a session that still
    # rebuilds in full on a later successful sweep.
    result.pruned = inbox.prune_processed(older_than_days) + inbox.prune_pending(
        older_than_days, routable_sessions
    )
    inbox.record_flush()
    return result
