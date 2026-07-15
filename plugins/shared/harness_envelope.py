"""Normalized harness-event envelope for Claude Code and Codex plugins.

This module is the producer side of the harness WAL (issue #997, SPEC-55).
It normalizes supported hook events into a shared envelope shape and provides
helpers for idempotency, redaction, and coalescing into Basic Memory artifacts.

Design constraints:
  - Stdlib only — no third-party imports. Both Claude Code (inline Python inside
    bash) and Codex (standalone scripts) must import this without an install step.
  - Never captures private model reasoning or hidden chain-of-thought.
  - Prefers summaries and metadata over raw transcript dumps.
  - Fails fast on missing project mapping rather than writing to the wrong project.

Usage from a hook script:

    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "shared"))
    from harness_envelope import create_envelope, to_provenance_observations

    envelope = create_envelope(
        event_type="compaction_imminent",
        source="claude-code",
        session_id=session_id,
        cwd=cwd,
        project_hint=primary_project,
        hook_name="PreCompact",
        payload_summary={"opening": clip(opening, 200)},
    )
    provenance_lines = to_provenance_observations(envelope)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# --- Event type constants ---
# V0 only captures events exposed through supported harness hooks.
# Future versions may add tool_called, file_changed, test_ran, etc.
# when PostToolUse hooks become available.

SESSION_STARTED = "session_started"
COMPACTION_IMMINENT = "compaction_imminent"
SESSION_ENDED = "session_ended"

V0_EVENT_TYPES = frozenset({SESSION_STARTED, COMPACTION_IMMINENT, SESSION_ENDED})

# --- Redaction defaults ---
# Keys whose values look like secrets. Matched case-insensitively against
# payload dict keys. Uses explicit patterns for common secret key naming
# conventions. The user can extend this via config (extra_redact_keys).
#
# Strategy: match keys that contain well-known secret indicators as full
# word segments (delimited by _ or . or at string boundaries). This catches
# API_KEY, AUTH_TOKEN, DB_PASSWORD but not "safe_key" or "monkey".
DEFAULT_REDACT_KEY_PATTERNS = (
    re.compile(r"(?i)(?:^|[_.])(?:SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTH)(?:[_.]|$)"),
    re.compile(r"(?i)(?:^|[_.])(?:API[_.]KEY|ACCESS[_.]KEY|PRIVATE[_.]KEY)(?:[_.]|$)"),
)

# Paths that should never appear in payload summaries.
DEFAULT_REDACT_PATHS = (
    os.path.expanduser("~/.ssh/"),
    os.path.expanduser("~/.aws/"),
    os.path.expanduser("~/.gnupg/"),
)

# Values that look like environment secrets: KEY=<long-value>
SECRET_VALUE_RE = re.compile(r"^[A-Za-z0-9_]+=.{20,}$")

# Maximum length for any single payload value before truncation.
MAX_PAYLOAD_VALUE_LEN = 500

# Maximum event log entries before rotation.
DEFAULT_EVENT_LOG_CAP = 1000


@dataclass(frozen=True)
class HarnessEnvelope:
    """Normalized event record from a Claude Code or Codex harness hook.

    This is the producer envelope shape from SPEC-55. Each field is chosen to
    be useful for coalescing into SessionNote / ToolLedger artifacts without
    requiring the downstream consumer to understand the raw hook payload format.
    """

    event_type: str
    source: str  # "claude-code" or "codex"
    session_id: str
    turn_id: str | None
    timestamp: str  # ISO 8601
    cwd: str
    project_hint: str  # basicMemory.primaryProject
    hook_name: str  # "SessionStart", "PreCompact"
    idempotency_key: str
    payload_summary: dict = field(default_factory=dict)


def idempotency_key(
    source: str,
    session_id: str,
    hook_name: str,
    timestamp: str,
) -> str:
    """Generate a deterministic key from (source, session_id, hook, timestamp_minute).

    Minute granularity means that repeated hooks within the same minute for the
    same session+hook combination produce the same key — preventing duplicate
    notes without requiring persistent state. Two hooks one minute apart get
    distinct keys, which is the right behavior (a second compaction a minute
    later is a genuinely new event).
    """
    # Truncate timestamp to minute precision for dedup window.
    # Handles both ISO format (2026-06-13T16:48:00+00:00) and plain timestamps.
    minute_key = timestamp[:16]  # "2026-06-13T16:48"
    raw = f"{source}:{session_id}:{hook_name}:{minute_key}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _redact_str(value: str, deny_paths: list[str]) -> str:
    """Apply the string-level redaction rules: secret values, paths, truncation."""
    if SECRET_VALUE_RE.match(value):
        return "[REDACTED]"
    if any(value.startswith(p) for p in deny_paths):
        return "[REDACTED_PATH]"
    if len(value) > MAX_PAYLOAD_VALUE_LEN:
        return value[:MAX_PAYLOAD_VALUE_LEN] + "…[truncated]"
    return value


def _redact_value(value, deny_key_patterns: list, deny_paths: list[str]):
    """Recursively redact a payload value of any JSON-compatible shape.

    Payloads arrive from hook JSON, so nested dicts and lists are normal —
    a secret one level down must be caught just like a top-level one.
    """
    if isinstance(value, str):
        return _redact_str(value, deny_paths)
    if isinstance(value, dict):
        return _redact_dict(value, deny_key_patterns, deny_paths)
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, deny_key_patterns, deny_paths) for item in value]
    return value


def _redact_dict(payload: dict, deny_key_patterns: list, deny_paths: list[str]) -> dict:
    result = {}
    for key, value in payload.items():
        # A denied key redacts the whole value, however deeply nested it is —
        # partial redaction inside a secret-named subtree is not worth the risk.
        if any(pat.search(str(key)) for pat in deny_key_patterns):
            result[key] = "[REDACTED]"
            continue
        result[key] = _redact_value(value, deny_key_patterns, deny_paths)
    return result


def redact_payload(
    payload: dict,
    extra_redact_keys: list[str] | None = None,
    extra_redact_paths: list[str] | None = None,
) -> dict:
    """Strip secrets, large values, and denied paths from a payload summary.

    Returns a new dict with sensitive content replaced by "[REDACTED]" markers,
    applied recursively over nested dicts and lists. This is the safety layer:
    nothing downstream sees unredacted payload values at any depth.
    """
    deny_key_patterns = list(DEFAULT_REDACT_KEY_PATTERNS)
    if extra_redact_keys:
        for pattern in extra_redact_keys:
            try:
                deny_key_patterns.append(re.compile(re.escape(pattern), re.IGNORECASE))
            except re.error:
                continue

    deny_paths = list(DEFAULT_REDACT_PATHS)
    if extra_redact_paths:
        deny_paths.extend(extra_redact_paths)

    return _redact_dict(payload, deny_key_patterns, deny_paths)


def create_envelope(
    *,
    event_type: str,
    source: str,
    session_id: str,
    cwd: str,
    project_hint: str,
    hook_name: str,
    turn_id: str | None = None,
    timestamp: str | None = None,
    payload_summary: dict | None = None,
    redact_keys: list[str] | None = None,
    redact_paths: list[str] | None = None,
) -> HarnessEnvelope:
    """Factory: build a normalized envelope from hook inputs.

    All arguments are keyword-only to prevent positional-order mistakes in
    hook scripts that construct envelopes from heterogeneous payload shapes.
    """
    if event_type not in V0_EVENT_TYPES:
        raise ValueError(
            f"Unknown event type {event_type!r}; v0 supports: {sorted(V0_EVENT_TYPES)}"
        )

    ts = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds")
    safe_payload = redact_payload(
        payload_summary or {},
        extra_redact_keys=redact_keys,
        extra_redact_paths=redact_paths,
    )

    idem_key = idempotency_key(source, session_id, hook_name, ts)

    return HarnessEnvelope(
        event_type=event_type,
        source=source,
        session_id=session_id,
        turn_id=turn_id,
        timestamp=ts,
        cwd=cwd,
        project_hint=project_hint,
        hook_name=hook_name,
        idempotency_key=idem_key,
        payload_summary=safe_payload,
    )


def to_provenance_observations(envelope: HarnessEnvelope) -> list[str]:
    """Convert an envelope into observation lines for a SessionNote body.

    These are appended to the "## Observations" section of the note. They
    stamp the note with its producer source so downstream consumers (recall,
    consolidation, memory routines) can trace provenance without storing the
    full raw event.
    """
    lines = [
        f"- [source] {envelope.source}/{envelope.session_id}",
        f"- [hook] {envelope.hook_name}",
        f"- [event] {envelope.event_type} at {envelope.timestamp}",
        f"- [idempotency] {envelope.idempotency_key}",
    ]
    if envelope.turn_id:
        lines.append(f"- [turn] {envelope.turn_id}")
    return lines


def to_frontmatter_fields(envelope: HarnessEnvelope) -> dict[str, str]:
    """Extract envelope fields suitable for inclusion in note frontmatter.

    These are added alongside the existing session/codex_session frontmatter
    to make the note queryable by source, hook, and idempotency key.
    """
    fields = {
        "envelope_source": envelope.source,
        "envelope_event": envelope.event_type,
        "envelope_hook": envelope.hook_name,
        "idempotency_key": envelope.idempotency_key,
    }
    if envelope.turn_id:
        fields["envelope_turn_id"] = envelope.turn_id
    return fields


def envelope_to_json(envelope: HarnessEnvelope) -> str:
    """Serialize an envelope to a compact JSON string for event log storage."""
    return json.dumps(asdict(envelope), separators=(",", ":"))


def append_to_event_log(
    envelope: HarnessEnvelope,
    cwd: str,
    cap: int = DEFAULT_EVENT_LOG_CAP,
) -> bool:
    """Append a serialized envelope to the local event log.

    The event log lives at <cwd>/.basic-memory/events.jsonl and stores raw
    envelopes for later coalescing by memory routines (SPEC-61). The log is
    capped at `cap` lines; when exceeded, the oldest half is rotated out.

    Returns True if the write succeeded, False on any error (best-effort).
    """
    log_dir = Path(cwd) / ".basic-memory"
    log_path = log_dir / "events.jsonl"

    try:
        log_dir.mkdir(parents=True, exist_ok=True)

        # Append the new event
        line = envelope_to_json(envelope) + "\n"
        with open(log_path, "a") as fh:
            fh.write(line)

        # --- Rotation check ---
        # Trigger: log exceeds the cap. Why: unbounded growth would fill disk
        # on long-running projects. Outcome: keep the newest half.
        try:
            with open(log_path) as fh:
                lines = fh.readlines()
            if len(lines) > cap:
                keep = lines[len(lines) // 2 :]
                with open(log_path, "w") as fh:
                    fh.writelines(keep)
        except Exception:
            pass  # rotation failure is non-fatal

        return True
    except Exception:
        return False
