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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from basic_memory.config import resolve_data_dir
from basic_memory.hooks._uuid7 import uuid7_unix_ms
from basic_memory.hooks.envelope import Envelope, envelope_to_json

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


def write_envelope(envelope: Envelope) -> Path:
    """Append an envelope to the inbox atomically.

    tmp + rename in the same directory: a crash mid-write leaves only a
    ``*.json.tmp`` straggler that ``list_envelopes`` never picks up — the inbox
    can never contain a half-written envelope.
    """
    directory = inbox_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{envelope.id}.json"
    # The uuid7 id is unique per envelope, so the tmp name cannot collide even
    # with concurrent hooks writing simultaneously.
    tmp = directory / f"{envelope.id}.json.tmp"
    tmp.write_text(envelope_to_json(envelope), encoding="utf-8")
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
    directory = processed_dir()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / path.name
    try:
        os.replace(path, destination)
    except FileNotFoundError:
        if destination.exists():
            return destination
        raise
    return destination


def prune_processed(older_than_days: int = DEFAULT_RETENTION_DAYS) -> int:
    """Delete processed envelopes older than the retention window.

    Age comes from the uuid7 timestamp embedded in the filename, not the file
    mtime — deterministic regardless of what filesystem operations touched the
    file since capture. Files that don't parse as UUIDs are never deleted:
    retention must not eat data it doesn't understand.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    removed = 0
    for path in processed_dir().glob("*.json"):
        try:
            captured_ms = uuid7_unix_ms(uuid.UUID(path.stem))
        except ValueError:
            continue
        if captured_ms < cutoff_ms:
            path.unlink()
            removed += 1
    return removed


# --- Flush bookkeeping (the `bm hook status` debuggability surface) ---


def record_flush(ts: str | None = None) -> None:
    """Stamp the last successful flush time for `bm hook status`."""
    directory = inbox_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = ts or datetime.now(timezone.utc).isoformat(timespec="seconds")
    (directory / LAST_FLUSH_FILE_NAME).write_text(stamp, encoding="utf-8")


def last_flush() -> str | None:
    """Return the last recorded flush timestamp, or None if never flushed."""
    marker = inbox_dir() / LAST_FLUSH_FILE_NAME
    if not marker.is_file():
        return None
    return marker.read_text(encoding="utf-8").strip()
