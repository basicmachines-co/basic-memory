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


@dataclass
class FlushResult:
    """What one projector sweep did, for `bm hook flush` / `status` reporting."""

    swept: int = 0  # envelope files seen in the inbox
    projected: int = 0  # envelopes promoted into artifacts
    duplicates: int = 0  # idempotency-key replays retired without writing
    pending: int = 0  # left in the inbox (no project mapping, or write failed)
    invalid: int = 0  # unreadable envelope files left in place
    pruned: int = 0  # processed envelopes removed by retention
    notes: list[str] = field(default_factory=list)  # artifact titles written


def split_project_ref(ref: str) -> tuple[str | None, str | None]:
    """Split a project reference into the (project, project_id) routing pair.

    A UUID reference must route via ``project_id``, not ``project``, or the
    call silently fails to land in a UUID-configured project.
    """
    if UUID_RE.match(ref):
        return None, ref
    return ref, None


def _processed_idempotency_keys() -> set[str]:
    """Idempotency keys already represented in artifacts (bounded by retention)."""
    keys: set[str] = set()
    for path in inbox.processed_dir().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("idempotency_key"), str):
            keys.add(data["idempotency_key"])
    return keys


def _short_session(session_id: str) -> str:
    return session_id[:8] or "unknown"


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
    title = f"Session {_short_session(session_id)} ({source})"
    frontmatter = _artifact_frontmatter("session", first)
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
    title = f"Tool Ledger {_short_session(session_id)} ({source})"
    frontmatter = _artifact_frontmatter("tool_ledger", first)

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


async def _write_artifact(title: str, content: str, folder: str, project_hint: str) -> None:
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
            entries.append((path, envelope_from_json(path.read_text(encoding="utf-8"))))
        except (ValueError, json.JSONDecodeError) as exc:
            # Trigger: corrupt or future-versioned envelope file.
            # Why: deleting it would destroy trace; projecting it would guess.
            # Outcome: left in place, counted, visible in `bm hook status`.
            logger.warning(f"skipping invalid envelope {path.name}: {exc}")
            result.invalid += 1

    # --- Group by session, preserving capture order within each group ---
    groups: dict[tuple[str, str], list[tuple[Path, Envelope]]] = {}
    for path, envelope in entries:
        groups.setdefault((envelope.source, envelope.source_session_id), []).append(
            (path, envelope)
        )

    seen_keys = _processed_idempotency_keys()

    for (source, session_id), group in groups.items():
        # --- Dedup: envelopes are hints, never double-write ---
        fresh: list[tuple[Path, Envelope]] = []
        replays: list[Path] = []
        group_keys: set[str] = set()
        for path, envelope in group:
            if envelope.idempotency_key in seen_keys or envelope.idempotency_key in group_keys:
                replays.append(path)
            else:
                group_keys.add(envelope.idempotency_key)
                fresh.append((path, envelope))

        # Replays duplicate either an already-projected envelope or an in-group
        # sibling that is being projected now — retire them without writing.
        for path in replays:
            inbox.mark_processed(path)
            result.duplicates += 1

        if not fresh:
            continue

        envelopes = [envelope for _, envelope in fresh]
        project_hint = next(
            (
                envelope.project_hint.strip()
                for envelope in envelopes
                if envelope.project_hint.strip()
            ),
            "",
        )
        if not project_hint:
            # Trigger: no project mapping was configured at capture time.
            # Why: writing to a default/guessed project would put trace in the
            #      wrong graph — the one unrecoverable failure mode.
            # Outcome: envelopes stay pending until a mapping resolves.
            result.pending += len(fresh)
            continue

        session_title, session_content = _session_note(source, session_id, envelopes)
        ledger_title, ledger_content = _tool_ledger_note(source, session_id, envelopes)
        folder = _capture_folder(envelopes)
        try:
            await _write_artifact(session_title, session_content, folder, project_hint)
            await _write_artifact(ledger_title, ledger_content, folder, project_hint)
        except Exception as exc:
            # Trigger: the write path failed (project missing, API error, ...).
            # Why: retiring unwritten envelopes would silently drop events.
            # Outcome: group stays pending; the next sweep re-derives it.
            logger.warning(f"flush left {source}/{session_id} pending: {exc}")
            result.pending += len(fresh)
            continue

        for path, _ in fresh:
            inbox.mark_processed(path)
            result.projected += 1
        result.notes += [session_title, ledger_title]

    result.pruned = inbox.prune_processed(older_than_days)
    inbox.record_flush()
    return result
