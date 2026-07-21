"""Private handoff state between Codex PreCompact and Stop hooks.

PreCompact cannot ask the active model to author durable memory, and its stdout
is ignored. It records a small local request instead. The next Stop hook turns
that request into a one-time continuation prompt for the same Codex turn.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from basic_memory.config import CONFIG_DIR_MODE, CONFIG_FILE_MODE, resolve_data_dir

REQUESTS_DIR_NAME = "checkpoint-requests"


@dataclass(frozen=True, slots=True)
class CheckpointRequest:
    source_session_id: str
    source_turn_id: str | None
    requested_at: str


def requests_dir() -> Path:
    return resolve_data_dir() / REQUESTS_DIR_NAME


def pending_count() -> int:
    """Count pending requests for the hook status surface."""
    return sum(1 for path in requests_dir().glob("*.json") if path.is_file())


def _ensure_private_dir() -> Path:
    directory = requests_dir()
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        resolve_data_dir().chmod(CONFIG_DIR_MODE)
        directory.chmod(CONFIG_DIR_MODE)
    return directory


def _request_path(session_id: str) -> Path:
    if not session_id:
        raise ValueError("checkpoint request requires a Codex session id")
    digest = hashlib.sha256(f"codex\0{session_id}".encode()).hexdigest()
    return requests_dir() / f"{digest}.json"


def _write(request: CheckpointRequest) -> Path:
    directory = _ensure_private_dir()
    target = _request_path(request.source_session_id)
    tmp = directory / f"{target.name}.{uuid.uuid4().hex}.tmp"
    tmp.write_text(json.dumps(asdict(request), sort_keys=True) + "\n", encoding="utf-8")
    if os.name != "nt":
        tmp.chmod(CONFIG_FILE_MODE)
    os.replace(tmp, target)
    return target


def create(session_id: str, turn_id: str | None) -> CheckpointRequest:
    """Create or replace the pending request for one Codex session."""
    request = CheckpointRequest(
        source_session_id=session_id,
        source_turn_id=turn_id,
        requested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    _write(request)
    return request


def read(session_id: str) -> CheckpointRequest | None:
    """Read a pending request, returning ``None`` when none exists."""
    path = _request_path(session_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(payload, dict):
        raise ValueError(f"invalid checkpoint request: {path}")
    try:
        source_session_id = payload["source_session_id"]
        source_turn_id = payload.get("source_turn_id")
        requested_at = payload["requested_at"]
    except KeyError as exc:
        raise ValueError(f"invalid checkpoint request: {path}") from exc
    if (
        not isinstance(source_session_id, str)
        or source_session_id != session_id
        or (source_turn_id is not None and not isinstance(source_turn_id, str))
        or not isinstance(requested_at, str)
    ):
        raise ValueError(f"invalid checkpoint request: {path}")
    return CheckpointRequest(
        source_session_id=source_session_id,
        source_turn_id=source_turn_id,
        requested_at=requested_at,
    )


def clear(session_id: str) -> None:
    """Clear a satisfied or deliberately abandoned request."""
    _request_path(session_id).unlink(missing_ok=True)
