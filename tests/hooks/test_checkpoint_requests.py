"""Tests for the private Codex PreCompact-to-Stop handshake state."""

import json
import os
import stat
from pathlib import Path

import pytest

from basic_memory.hooks import checkpoint_requests


def test_checkpoint_request_lifecycle_is_private(bm_home: Path) -> None:
    request = checkpoint_requests.create("session-1", "turn-1")

    assert checkpoint_requests.pending_count() == 1
    assert checkpoint_requests.read("session-1") == request

    paths = list(checkpoint_requests.requests_dir().glob("*.json"))
    assert len(paths) == 1
    if os.name != "nt":
        assert stat.S_IMODE(checkpoint_requests.requests_dir().stat().st_mode) == 0o700
        assert stat.S_IMODE(paths[0].stat().st_mode) == 0o600

    checkpoint_requests.clear("session-1")
    assert checkpoint_requests.read("session-1") is None
    assert checkpoint_requests.pending_count() == 0


def test_checkpoint_request_replaces_same_session_atomically(bm_home: Path) -> None:
    first = checkpoint_requests.create("session-1", "turn-1")
    second = checkpoint_requests.create("session-1", "turn-2")

    assert second.requested_at >= first.requested_at
    assert checkpoint_requests.read("session-1") == second
    assert checkpoint_requests.pending_count() == 1
    assert not list(checkpoint_requests.requests_dir().glob("*.tmp"))


def test_checkpoint_request_requires_session_id(bm_home: Path) -> None:
    with pytest.raises(ValueError, match="requires a Codex session id"):
        checkpoint_requests.create("", None)


def test_checkpoint_request_rejects_non_object(bm_home: Path) -> None:
    checkpoint_requests.create("session-1", None)
    path = next(checkpoint_requests.requests_dir().glob("*.json"))
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid checkpoint request"):
        checkpoint_requests.read("session-1")


def test_checkpoint_request_rejects_missing_fields(bm_home: Path) -> None:
    checkpoint_requests.create("session-1", None)
    path = next(checkpoint_requests.requests_dir().glob("*.json"))
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid checkpoint request"):
        checkpoint_requests.read("session-1")


@pytest.mark.parametrize(
    "override",
    [
        {"source_session_id": "other"},
        {"source_turn_id": 42},
        {"requested_at": 42},
    ],
)
def test_checkpoint_request_rejects_invalid_field_types(
    bm_home: Path, override: dict[str, object]
) -> None:
    checkpoint_requests.create("session-1", None)
    path = next(checkpoint_requests.requests_dir().glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(override)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid checkpoint request"):
        checkpoint_requests.read("session-1")
