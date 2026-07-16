"""SPEC-55 producer envelope for harness lifecycle events.

Adapted from ``plugins/shared/harness_envelope.py`` on the #1064 salvage branch
(credit: sourrrish) — the contract shape, idempotency keying, and provenance
projections proven there carry into core, extended with the 2026-07-15
revision fields: ``id`` (UUIDv7), ``actor``, ``caused_by``, and
``promotion_status``.

Envelopes are trace, not memory: they stay ``promotion_status: raw`` until a
projector promotes them. The idempotency key is computed from metadata only,
so redaction never changes identity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import MISSING, asdict, dataclass, field, fields
from datetime import datetime, timezone

from basic_memory.hooks._uuid7 import uuid7
from basic_memory.hooks.redaction import redact_payload, redact_text

ENVELOPE_VERSION = 1

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

# Fields the projector treats as strings (groups on, sorts by, calls .strip()).
# A corrupt inbox file can carry the right keys with wrong scalar types, which
# the dataclass won't reject — validate them before constructing so a bad file
# is counted invalid, not allowed to crash the sweep.
_STR_FIELDS = (
    "id",
    "source",
    "event",
    "source_session_id",
    "ts",
    "cwd",
    "project_hint",
    "idempotency_key",
    "actor",
    "promotion_status",
)
_OPTIONAL_STR_FIELDS = ("source_turn_id", "caused_by")


@dataclass(frozen=True)
class Envelope:
    """Normalized event record from a harness lifecycle hook (SPEC-55 Contract 1).

    Each field is chosen so the downstream consumer (the projector today, the
    SPEC-54 worker later) can coalesce SessionNote / ToolLedger artifacts
    without understanding raw hook payload formats.
    """

    id: str  # UUIDv7 — doubles as the inbox filename and caused_by target
    source: str  # "claude-code" | "codex" (enum grows per SPEC-55 registry)
    event: str  # one of V0_EVENTS
    source_session_id: str  # opaque, surface-defined
    ts: str  # ISO 8601
    cwd: str
    project_hint: str  # consumers fail fast when this doesn't resolve
    idempotency_key: str  # sha256(source:session:event:ts-to-minute)[:16]
    envelope_version: int = ENVELOPE_VERSION
    source_turn_id: str | None = None
    actor: str = ACTOR_RUNTIME  # "runtime" | "user" | routine name
    caused_by: str | None = None  # id of the triggering event, when known
    promotion_status: str = PROMOTION_RAW
    payload: dict = field(default_factory=dict)  # redacted summary only


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
    payload: dict | None = None,
    extra_redact_keys: list[str] | None = None,
    extra_redact_paths: list[str] | None = None,
) -> Envelope:
    """Factory: build a producer envelope from normalized hook inputs.

    Keyword-only to prevent positional-order mistakes when callers construct
    envelopes from heterogeneous payload shapes. Both the payload and the ``cwd``
    pass through the Stage-1 redaction floor here, at the factory — no envelope
    built through this path can carry unredacted payload values or a denied
    workspace path into the inbox. ``project_hint`` is a project name, not a
    path, so it is left intact (the projector resolves against it).
    """
    if event not in V0_EVENTS:
        raise ValueError(f"Unknown event {event!r}; v0 supports: {sorted(V0_EVENTS)}")

    resolved_ts = ts or datetime.now(timezone.utc).isoformat(timespec="seconds")
    safe_payload = redact_payload(
        payload or {},
        extra_redact_keys=extra_redact_keys,
        extra_redact_paths=extra_redact_paths,
    )
    # cwd is a user path: a session under a configured redactPaths (or a default
    # deny dir) must not persist the raw path in the inbox WAL.
    safe_cwd = redact_text(cwd, extra_redact_paths=extra_redact_paths)

    return Envelope(
        id=str(uuid7()),
        source=source,
        event=event,
        source_session_id=session_id,
        source_turn_id=turn_id,
        ts=resolved_ts,
        cwd=safe_cwd,
        project_hint=project_hint,
        actor=actor,
        caused_by=caused_by,
        idempotency_key=idempotency_key(source, session_id, event, resolved_ts),
        payload=safe_payload,
    )


# --- Projections into Basic Memory artifacts ---


def to_provenance_observations(envelope: Envelope) -> list[str]:
    """Observation lines stamping an artifact with its producer provenance.

    Appended to a note's "## Observations" section so downstream consumers
    (recall, consolidation, memory routines) can trace where the artifact came
    from without storing the raw event. The ``[source]`` observation is the one
    SPEC-55 requires on every projected artifact.
    """
    lines = [
        f"- [source] {envelope.source}/{envelope.source_session_id}",
        f"- [event] {envelope.event} at {envelope.ts}",
        f"- [idempotency] {envelope.idempotency_key}",
    ]
    if envelope.source_turn_id:
        lines.append(f"- [turn] {envelope.source_turn_id}")
    return lines


def to_frontmatter_fields(envelope: Envelope) -> dict[str, str]:
    """Envelope fields suitable for note frontmatter.

    Makes projected artifacts queryable by source, event, envelope id, and
    idempotency key through metadata search.
    """
    fields_out = {
        "envelope_id": envelope.id,
        "envelope_source": envelope.source,
        "envelope_event": envelope.event,
        "idempotency_key": envelope.idempotency_key,
    }
    if envelope.source_turn_id:
        fields_out["envelope_turn_id"] = envelope.source_turn_id
    return fields_out


# --- Serialization ---


def envelope_to_json(envelope: Envelope) -> str:
    """Serialize an envelope to a compact JSON string for inbox storage."""
    return json.dumps(asdict(envelope), separators=(",", ":"))


def envelope_from_json(text: str) -> Envelope:
    """Parse an inbox file back into an Envelope, failing fast on junk.

    The inbox is a durable WAL that outlives code versions; a shape mismatch
    means either corruption or a future envelope_version — both must surface as
    an error the projector can count, never as a silently misread event.
    """
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("envelope JSON must be an object")

    field_names = {f.name for f in fields(Envelope)}
    required = {
        f.name for f in fields(Envelope) if f.default is MISSING and f.default_factory is MISSING
    }
    unknown = set(data) - field_names
    missing = required - set(data)
    if unknown or missing:
        raise ValueError(
            f"envelope shape mismatch (unknown={sorted(unknown)}, missing={sorted(missing)})"
        )
    if not isinstance(data.get("payload", {}), dict):
        raise ValueError("envelope payload must be an object")

    # Scalar type checks: keys can be present with the wrong type (e.g.
    # "source_session_id": [], "project_hint": 1). bool is excluded from the int
    # check since it is an int subclass.
    for name in _STR_FIELDS:
        if name in data and not isinstance(data[name], str):
            raise ValueError(f"envelope field {name!r} must be a string")
    for name in _OPTIONAL_STR_FIELDS:
        if data.get(name) is not None and not isinstance(data[name], str):
            raise ValueError(f"envelope field {name!r} must be a string or null")
    version = data.get("envelope_version")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int)):
        raise ValueError("envelope field 'envelope_version' must be an int")

    envelope = Envelope(**data)
    if envelope.envelope_version != ENVELOPE_VERSION:
        raise ValueError(f"unsupported envelope_version {envelope.envelope_version!r}")
    return envelope
