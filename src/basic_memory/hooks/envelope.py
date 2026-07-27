"""SPEC-55 producer envelope for harness lifecycle events.

Adapted from ``plugins/shared/harness_envelope.py`` on the #1064 salvage branch
(credit: sourrrish) — the contract shape and idempotency keying proven there
carry into core, extended with the 2026-07-15 revision fields: ``id`` (UUIDv7),
``actor``, ``caused_by``, and ``promotion_status``.

Envelopes are trace, not memory: they remain ``promotion_status: raw`` and are
archived locally rather than promoted into the graph. The idempotency key is
computed from metadata only, so bounded payload changes never change identity.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from basic_memory.hooks._uuid7 import uuid7
from typing import Any

ENVELOPE_VERSION = 1

# A harness session, identified by its producing surface and opaque session id.
# Retained as a named compatibility type for trace consumers.
type SessionKey = tuple[str, str]

# --- Event registry ---
# V0 ships the three events exposed through supported harness hooks. The other
# nine SPEC-55 events (tool_called, file_changed, ...) wait for real hook
# support (PostToolUse et al.).
SESSION_STARTED = "session_started"
COMPACTION_IMMINENT = "compaction_imminent"
SESSION_ENDED = "session_ended"
V0_EVENTS = frozenset({SESSION_STARTED, COMPACTION_IMMINENT, SESSION_ENDED})

# Promotion ladder: raw -> summarized -> candidate -> accepted / rejected.
# Agents propose memory; they don't silently create it.
PROMOTION_RAW = "raw"

# Actor when the harness runtime itself produced the event (vs a user action
# or a named routine).
ACTOR_RUNTIME = "runtime"


class Envelope(BaseModel):
    """Normalized event record from a harness lifecycle hook (SPEC-55 Contract 1).

    Each field records stable operational identity without requiring a consumer
    to understand raw hook payload formats.

    This is a persistence boundary: the inbox is a durable WAL that outlives code
    versions, so parsing must fail fast on junk — a shape mismatch means
    corruption or a future ``envelope_version``, never a silently misread event.
    ``strict`` (no scalar coercion — a corrupt file's ``"source": []`` is
    rejected, not stringified), ``extra="forbid"`` (unknown keys rejected), and
    ``frozen`` (envelopes are immutable trace) enforce that at the type layer,
    replacing the hand-rolled field/scalar checks this model used to carry.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    id: str  # UUIDv7 — doubles as the inbox filename and caused_by target
    source: str  # "claude-code" | "codex" (enum grows per SPEC-55 registry)
    event: str  # one of V0_EVENTS
    source_session_id: str  # opaque, surface-defined
    ts: str  # ISO 8601
    cwd: str
    project_hint: str  # mapping at capture time; trace only, never an implicit write route
    idempotency_key: str  # sha256(source:session:event:ts-to-minute)[:16]
    envelope_version: int = ENVELOPE_VERSION
    source_turn_id: str | None = None
    actor: str = ACTOR_RUNTIME  # "runtime" | "user" | routine name
    caused_by: str | None = None  # id of the triggering event, when known
    promotion_status: str = PROMOTION_RAW
    payload: dict[str, Any] = Field(
        default_factory=dict[str, Any]
    )  # bounded lifecycle metadata only

    @field_validator("envelope_version")
    @classmethod
    def _supported_version(cls, version: int) -> int:
        # A future version is a forward-compat signal, not corruption — surface
        # it as an error the archive sweep counts and `bm hook status` shows.
        if version != ENVELOPE_VERSION:
            raise ValueError(f"unsupported envelope_version {version!r}")
        return version

    @property
    def session_key(self) -> SessionKey:
        """The ``(source, session_id)`` pair the inbox groups and routes by."""
        return (self.source, self.source_session_id)


def idempotency_key(source: str, session_id: str, event: str, ts: str) -> str:
    """Deterministic key from (source, session, event, timestamp-minute).

    Minute granularity means repeated hooks within the same minute for the same
    session+event produce the same key — stateless dedup without persistent
    bookkeeping. Two hooks a minute apart get distinct keys, which is correct:
    a second compaction a minute later is a genuinely new event.

    The event name plays the SPEC-55 "hook" role in the key: v0 events map 1:1
    onto harness hooks (session_started↔SessionStart, compaction_imminent↔
    PreCompact, session_ended↔SessionEnd).
    """
    minute_key = ts[:16]  # "2026-07-15T10:00"
    raw = f"{source}:{session_id}:{event}:{minute_key}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def create_envelope(
    *,
    source: str,
    event: str,
    session_id: str,
    cwd: str,
    project_hint: str,
    turn_id: str | None = None,
    ts: str | None = None,
    actor: str = ACTOR_RUNTIME,
    caused_by: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Envelope:
    """Factory: build a producer envelope from normalized hook inputs.

    Keyword-only to prevent positional-order mistakes when callers construct
    envelopes from heterogeneous payload shapes. Callers keep payloads bounded
    to lifecycle metadata; the factory validates the stable envelope contract
    before it enters the inbox.
    """
    if event not in V0_EVENTS:
        raise ValueError(f"Unknown event {event!r}; v0 supports: {sorted(V0_EVENTS)}")

    resolved_ts = ts or datetime.now(timezone.utc).isoformat(timespec="seconds")

    return Envelope(
        id=str(uuid7()),
        source=source,
        event=event,
        source_session_id=session_id,
        source_turn_id=turn_id,
        ts=resolved_ts,
        cwd=cwd,
        project_hint=project_hint,
        actor=actor,
        caused_by=caused_by,
        idempotency_key=idempotency_key(source, session_id, event, resolved_ts),
        payload=dict(payload or {}),
    )


# --- Serialization ---


def envelope_to_json(envelope: Envelope) -> str:
    """Serialize an envelope to a compact JSON string for inbox storage."""
    return envelope.model_dump_json()


def envelope_from_json(text: str) -> Envelope:
    """Parse an inbox file back into an Envelope, failing fast on junk.

    Delegates to the model's strict validation (see :class:`Envelope`): a shape
    mismatch, wrong scalar type, unknown key, or unsupported version raises a
    ``pydantic.ValidationError`` — itself a ``ValueError`` — which the archive
    sweep catches and counts.
    """
    return Envelope.model_validate_json(text)
