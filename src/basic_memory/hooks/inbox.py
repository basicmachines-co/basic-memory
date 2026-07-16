"""The harness event inbox: an append-only local WAL (SPEC-55).

One JSON file per envelope, named ``<uuid7>.json`` so plain filename order is
chronological capture order. Lives under the Basic Memory home dir *by
requirement*, not preference: plugin directories are ephemeral
(``CLAUDE_PLUGIN_ROOT`` changes every update, ``CLAUDE_PLUGIN_DATA`` is deleted
on uninstall) and uninstalling a plugin must never delete captured memory
trace.

No structure is written at capture time — ever. Processed envelopes move to
``processed/`` for audit and are pruned after a retention window.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from basic_memory.config import CONFIG_DIR_MODE, CONFIG_FILE_MODE, resolve_data_dir
from basic_memory.hooks._uuid7 import uuid7_unix_ms
from basic_memory.hooks.envelope import (
    Envelope,
    SessionKey,
    envelope_from_json,
    envelope_to_json,
)

INBOX_DIR_NAME = "inbox"
PROCESSED_DIR_NAME = "processed"
LAST_FLUSH_FILE_NAME = ".last-flush"

DEFAULT_RETENTION_DAYS = 30


def inbox_dir() -> Path:
    # resolve_data_dir() is core's single source of truth for the per-user
    # state directory (BASIC_MEMORY_CONFIG_DIR > XDG_CONFIG_HOME > ~/.basic-memory).
    return resolve_data_dir() / INBOX_DIR_NAME


def processed_dir() -> Path:
    return inbox_dir() / PROCESSED_DIR_NAME


# The WAL holds cwd, project names, session ids, and model metadata, so it must
# be owner-only — matching the modes config dirs/files use. This matters most on
# the hook path, which can create ~/.basic-memory ahead of normal config init
# (which would otherwise set these), leaving mkdir/write at the default umask.
def _secure_dir(path: Path) -> None:
    if os.name != "nt":  # Windows has no comparable owner-only mode
        path.chmod(CONFIG_DIR_MODE)


def _secure_file(path: Path) -> None:
    if os.name != "nt":
        path.chmod(CONFIG_FILE_MODE)


def _ensure_private_dir(path: Path) -> Path:
    """Create ``path`` (and parents) and lock it — plus the state root, which the
    hook may have created ahead of config init — down to owner-only (0700)."""
    path.mkdir(parents=True, exist_ok=True)
    _secure_dir(resolve_data_dir())
    _secure_dir(path)
    return path


def write_envelope(envelope: Envelope) -> Path:
    """Append an envelope to the inbox atomically.

    tmp + rename in the same directory: a crash mid-write leaves only a
    ``*.json.tmp`` straggler that ``list_envelopes`` never picks up — the inbox
    can never contain a half-written envelope.
    """
    directory = _ensure_private_dir(inbox_dir())
    target = directory / f"{envelope.id}.json"
    # The uuid7 id is unique per envelope, so the tmp name cannot collide even
    # with concurrent hooks writing simultaneously.
    tmp = directory / f"{envelope.id}.json.tmp"
    tmp.write_text(envelope_to_json(envelope), encoding="utf-8")
    # Lock the tmp before the rename so the published file is owner-only from the
    # moment it appears (os.replace preserves the source's mode).
    _secure_file(tmp)
    os.replace(tmp, target)
    return target


def list_envelopes() -> list[Path]:
    """Pending envelope files in capture order (uuid7 filenames sort chronologically)."""
    return sorted(path for path in inbox_dir().glob("*.json") if path.is_file())


def mark_processed(path: Path) -> Path:
    """Retire a projected envelope into processed/ (kept for audit, then pruned).

    Tolerant of a concurrent flush that already retired this envelope: a missing
    source with the destination already present means another sweep moved it
    first, so return that instead of aborting the current sweep midway.
    """
    directory = _ensure_private_dir(processed_dir())
    destination = directory / path.name
    try:
        # os.replace preserves the source file's owner-only mode into processed/.
        os.replace(path, destination)
    except FileNotFoundError:
        if destination.exists():
            return destination
        raise
    return destination


def _unresolvable_pending_gate(
    routable_sessions: frozenset[SessionKey],
) -> Callable[[Path], bool]:
    """Build the prune gate: a pending envelope is unresolvable only if it can
    *never* flush — it parses, carries no project hint, and its session is not
    routable through any sibling.

    Kept (not pruned): a parse failure (corrupt/future-versioned — the trace
    ``bm hook status`` surfaces for a human); a present hint (mapped, pending
    only because a write failed and must self-heal); and — crucially — a
    hint-less file whose ``(source, session_id)`` appears in ``routable_sessions``
    (another envelope in that session, pending or already processed, carries a
    hint, so the group self-heals and rebuilds the full session on a later sweep).
    """

    def gate(path: Path) -> bool:
        try:
            envelope = envelope_from_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # ValueError covers json.JSONDecodeError
            return False
        if envelope.project_hint.strip():
            return False
        return envelope.session_key not in routable_sessions

    return gate


def _prune_dir(
    directory: Path,
    older_than_days: int,
    *,
    should_prune: Callable[[Path], bool] | None = None,
) -> int:
    """Delete ``*.json`` in ``directory`` older than the retention window.

    Age comes from the uuid7 timestamp embedded in the filename, not the file
    mtime — deterministic regardless of what filesystem operations touched the
    file since capture. Files whose name doesn't parse as a UUID are never
    deleted: retention must not eat data it doesn't understand. The glob is
    non-recursive, so pruning the inbox never reaches into ``processed/``.

    ``should_prune`` (when given) is a final gate on an otherwise-expired file:
    only files it returns True for are deleted. The inbox uses it to prune solely
    the unresolvable trace, never a corrupt file or a mapped write-failure that
    should self-heal.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    removed = 0
    for path in directory.glob("*.json"):
        if not path.is_file():
            continue
        try:
            captured_ms = uuid7_unix_ms(uuid.UUID(path.stem))
        except ValueError:
            continue
        if captured_ms >= cutoff_ms:
            continue
        if should_prune is not None and not should_prune(path):
            continue
        path.unlink()
        removed += 1
    return removed


def prune_processed(older_than_days: int = DEFAULT_RETENTION_DAYS) -> int:
    """Delete processed envelopes older than the retention window."""
    return _prune_dir(processed_dir(), older_than_days)


def prune_pending(
    older_than_days: int = DEFAULT_RETENTION_DAYS,
    routable_sessions: frozenset[SessionKey] = frozenset(),
) -> int:
    """Delete pending envelopes older than the retention window.

    A session that never resolves a project mapping (``primaryProject`` unset for
    its whole lifetime) produces envelopes the projector can never route — it
    holds them pending, waiting for a mapping that, for a fully-unmapped session,
    never comes. Bounding the inbox by the same window the processed side already
    uses keeps that unresolvable trace from accumulating without limit, while
    still giving a mapping the full window to appear (a later same-session
    capture carrying a hint resolves the whole group via the projector's merge).

    ``routable_sessions`` is the set of ``(source, session_id)`` the caller knows
    to be routable (a hinted envelope somewhere in the session — pending or
    processed). Only *unresolvable* pending entries are pruned: a corrupt file, a
    mapped write-failure, and a hint-less file belonging to a routable session
    are all left in place, so retention never defeats self-heal (the session
    still rebuilds in full on a later sweep) or eats the corruption trace
    ``bm hook status`` surfaces.
    """
    return _prune_dir(
        inbox_dir(),
        older_than_days,
        should_prune=_unresolvable_pending_gate(routable_sessions),
    )


# --- Flush bookkeeping (the `bm hook status` debuggability surface) ---


def record_flush(ts: str | None = None) -> None:
    """Stamp the last successful flush time for `bm hook status`."""
    directory = _ensure_private_dir(inbox_dir())
    stamp = ts or datetime.now(timezone.utc).isoformat(timespec="seconds")
    marker = directory / LAST_FLUSH_FILE_NAME
    marker.write_text(stamp, encoding="utf-8")
    _secure_file(marker)


def last_flush() -> str | None:
    """Return the last recorded flush timestamp, or None if never flushed."""
    marker = inbox_dir() / LAST_FLUSH_FILE_NAME
    if not marker.is_file():
        return None
    return marker.read_text(encoding="utf-8").strip()
