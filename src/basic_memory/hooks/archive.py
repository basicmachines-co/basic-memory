"""Retire harness lifecycle envelopes into the local audit archive.

``bm hook flush`` is an operational WAL sweep, not a memory-authoring path.
Valid envelopes move to the owner-private ``processed/`` archive, while invalid
envelopes remain in the inbox for inspection. Nothing here writes to the graph.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from loguru import logger

from basic_memory.hooks import inbox
from basic_memory.hooks.envelope import Envelope, envelope_from_json


@dataclass
class FlushResult:
    """What one local trace sweep did."""

    swept: int = 0
    archived: int = 0
    duplicates: int = 0
    pending: int = 0
    invalid: int = 0
    pruned: int = 0
    skipped: bool = False


def _processed_idempotency_keys() -> set[str]:
    """Read replay keys already present in the local audit archive."""
    keys: set[str] = set()
    for path in inbox.processed_dir().glob("*.json"):
        try:
            envelope = envelope_from_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        keys.add(envelope.idempotency_key)
    return keys


async def flush(older_than_days: int = inbox.DEFAULT_RETENTION_DAYS) -> FlushResult:
    """Archive the pending lifecycle trace without creating graph notes.

    A non-blocking lock serializes sweeps. Valid envelopes are retired locally
    regardless of project mapping because no graph routing occurs. Corrupt or
    future-versioned envelopes stay pending so an operator can inspect them.
    """
    with inbox.flush_lock() as acquired:
        if not acquired:
            logger.debug("flush skipped: another flush holds the inbox lock")
            return FlushResult(skipped=True)
        return _flush_locked(older_than_days)


def _flush_locked(older_than_days: int) -> FlushResult:
    """Archive one inbox snapshot while :func:`flush` holds the lock."""
    result = FlushResult()
    seen_keys = _processed_idempotency_keys()

    for path in inbox.list_envelopes():
        result.swept += 1
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            # A file can vanish after listing when another process owns it. It
            # is neither corrupt nor proof of a failed archive, so skip it.
            logger.debug(f"skipping unreadable envelope {path.name}: {exc}")
            continue

        try:
            envelope: Envelope = envelope_from_json(text)
        except (ValueError, json.JSONDecodeError) as exc:
            # Unknown trace must remain observable; never delete or reinterpret it.
            logger.warning(f"skipping invalid envelope {path.name}: {exc}")
            result.invalid += 1
            continue

        duplicate = envelope.idempotency_key in seen_keys
        try:
            inbox.mark_processed(path)
        except OSError as exc:
            logger.warning(f"flush could not archive {path.name}: {exc}")
            result.pending += 1
            continue

        if duplicate:
            result.duplicates += 1
        else:
            seen_keys.add(envelope.idempotency_key)
            result.archived += 1

    result.pruned = inbox.prune_processed(older_than_days)
    inbox.record_flush()
    return result
