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

Packaging: plugin marketplaces install a single plugin directory, so this
canonical copy in plugins/shared/ never ships. scripts/sync_plugin_shared.py
vendors it into each plugin's hooks/ directory (verified in package-check), and
hooks import it from their own directory:

    import sys, os
    sys.path.insert(0, hook_dir)  # the directory containing the hook script
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


# Paths that should never appear in payload summaries. Deny paths and payload
# values are both compared with forward slashes — os.path.expanduser("~/.ssh/")
# yields mixed separators on Windows (C:\Users\x/.ssh/) while native payload
# values use backslashes, so an un-normalized startswith never matches there.
def _normalize_path(path: str) -> str:
    return path.replace("\\", "/")


DEFAULT_REDACT_PATHS = (
    _normalize_path(os.path.expanduser("~/.ssh/")),
    _normalize_path(os.path.expanduser("~/.aws/")),
    _normalize_path(os.path.expanduser("~/.gnupg/")),
)

# Values that look like environment secrets: KEY=<long-value>
SECRET_VALUE_RE = re.compile(r"^[A-Za-z0-9_]+=.{20,}$")

# Maximum length for any single payload value before truncation.
MAX_PAYLOAD_VALUE_LEN = 500

# Maximum event log entries before rotation.
DEFAULT_EVENT_LOG_CAP = 1000

# Rotation is size-triggered (a single stat per append), so the cap in lines is
# converted to a byte threshold using this conservative per-entry estimate.
# Envelope JSON lines run ~300-600 bytes (payload values truncate at 500 chars).
APPROX_BYTES_PER_EVENT = 512


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
    if any(_normalize_path(value).startswith(p) for p in deny_paths):
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
        deny_paths.extend(_normalize_path(path) for path in extra_redact_paths)

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


def _events_root() -> Path:
    """Resolve the root directory for local event logs.

    Mirrors core's ``basic_memory.config.resolve_data_dir()`` (stdlib-only, so
    no import): ``BASIC_MEMORY_CONFIG_DIR`` first, then ``XDG_CONFIG_HOME``,
    then ``~/.basic-memory``. Event logs live under the per-user Basic Memory
    data dir — never inside the working directory, where they would dirty the
    user's repository.
    """
    if config_dir := os.environ.get("BASIC_MEMORY_CONFIG_DIR"):
        return Path(config_dir) / "events"
    if xdg_config := os.environ.get("XDG_CONFIG_HOME"):
        return Path(xdg_config) / "basic-memory" / "events"
    return Path.home() / ".basic-memory" / "events"


def _event_log_slug(project_hint: str, cwd: str) -> str:
    """Directory name isolating one project's event log from another's.

    Prefers the configured project (stable across checkouts of the same
    project); falls back to the working directory. A short digest of the raw
    seed disambiguates values that slug identically ("my/proj" vs "my-proj").
    """
    seed = (project_hint or "").strip() or cwd
    slug = re.sub(r"[^a-z0-9]+", "-", seed.lower()).strip("-") or "default"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
    return f"{slug[:48]}-{digest}"


def event_log_path(envelope: HarnessEnvelope) -> Path:
    """Compute where this envelope's event log lives (see _events_root)."""
    return _events_root() / _event_log_slug(envelope.project_hint, envelope.cwd) / "events.jsonl"


def _normalize_cap(cap) -> int:
    # Trigger: eventRetention comes straight from user JSON config.
    # Why: a junk value must neither break best-effort logging nor unbound the
    #      log. Outcome: positive ints win; anything else uses the default cap.
    if isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0:
        return DEFAULT_EVENT_LOG_CAP
    return cap


def append_to_event_log(
    envelope: HarnessEnvelope,
    cap: int | None = None,
) -> bool:
    """Append a serialized envelope to the local event log.

    The event log lives under the Basic Memory data dir at
    ``<data-dir>/events/<project-or-cwd-slug>/events.jsonl`` and stores raw
    envelopes for later coalescing by memory routines (SPEC-61). ``cap`` is the
    approximate retention limit in entries (the ``eventRetention`` setting);
    ``None`` uses DEFAULT_EVENT_LOG_CAP.

    The hot path stays cheap: one append plus one stat. Rotation only runs when
    the file size passes ``cap * APPROX_BYTES_PER_EVENT`` bytes; it then drops
    the oldest half (bounded at ``cap`` lines), so the file halves and the next
    many appends are stat-only again.

    Returns True if the write succeeded, False on any error (best-effort).
    """
    cap = _normalize_cap(cap)
    log_path = event_log_path(envelope)

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Append the new event
        line = envelope_to_json(envelope) + "\n"
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(line)

        # --- Rotation check (stat-only on the hot path) ---
        # Trigger: log size passes the byte threshold derived from the cap.
        # Why: unbounded growth would fill disk on long-running projects, but
        #      counting lines on every hook write would make the hot path O(n).
        # Outcome: the oldest half rotates out; retention is approximate.
        try:
            if log_path.stat().st_size > cap * APPROX_BYTES_PER_EVENT:
                with open(log_path, encoding="utf-8") as fh:
                    lines = fh.readlines()
                keep = lines[len(lines) // 2 :][-cap:]
                with open(log_path, "w", encoding="utf-8") as fh:
                    fh.writelines(keep)
        except Exception:
            pass  # rotation failure is non-fatal

        return True
    except Exception:
        return False
