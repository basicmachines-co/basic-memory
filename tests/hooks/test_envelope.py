"""Unit tests for the SPEC-55 producer envelope contract."""

import json
import uuid

import pytest

from basic_memory.hooks.envelope import (
    ACTOR_RUNTIME,
    COMPACTION_IMMINENT,
    ENVELOPE_VERSION,
    PROMOTION_RAW,
    create_envelope,
    envelope_from_json,
    envelope_to_json,
    idempotency_key,
)


def _envelope(**overrides):
    kwargs: dict = {
        "source": "claude-code",
        "event": COMPACTION_IMMINENT,
        "session_id": "session-1",
        "cwd": "/tmp/workdir",
        "project_hint": "test-project",
        "ts": "2026-07-15T10:00:00+00:00",
    }
    kwargs.update(overrides)
    return create_envelope(**kwargs)


# --- Contract shape ---


def test_envelope_contract_defaults() -> None:
    envelope = _envelope()

    assert uuid.UUID(envelope.id).version == 7  # id is a UUIDv7
    assert envelope.envelope_version == ENVELOPE_VERSION == 1
    assert envelope.promotion_status == PROMOTION_RAW
    assert envelope.actor == ACTOR_RUNTIME
    assert envelope.caused_by is None
    assert envelope.source_turn_id is None
    assert envelope.project_hint == "test-project"
    assert len(envelope.idempotency_key) == 16
    int(envelope.idempotency_key, 16)  # 16 hex chars


def test_envelope_carries_actor_caused_by_and_turn() -> None:
    envelope = _envelope(actor="user", caused_by="0198-parent", turn_id="turn-9")

    assert envelope.actor == "user"
    assert envelope.caused_by == "0198-parent"
    assert envelope.source_turn_id == "turn-9"


def test_create_envelope_rejects_unknown_event() -> None:
    with pytest.raises(ValueError, match="Unknown event"):
        _envelope(event="tool_called")


def test_create_envelope_defaults_ts_to_now() -> None:
    envelope = _envelope(ts=None)

    assert envelope.ts.startswith("20")
    assert "T" in envelope.ts


def test_create_envelope_redacts_payload_recursively() -> None:
    envelope = _envelope(payload={"nested": {"password": "p" * 30}, "note": "safe"})

    assert envelope.payload["nested"]["password"] == "[REDACTED]"
    assert envelope.payload["note"] == "safe"


def test_create_envelope_redacts_cwd_under_denied_path() -> None:
    # cwd is a user path; a session under a configured redactPaths dir must not
    # persist the raw path into the inbox WAL.
    envelope = _envelope(
        cwd="/srv/clients/acme/repo",
        extra_redact_paths=["/srv/clients/"],
    )

    assert envelope.cwd == "[REDACTED_PATH]"


def test_create_envelope_keeps_ordinary_cwd() -> None:
    envelope = _envelope(cwd="/home/dev/project")

    assert envelope.cwd == "/home/dev/project"


# --- Idempotency ---


def test_idempotency_key_is_stable_within_the_same_minute() -> None:
    key_a = idempotency_key("codex", "s-1", COMPACTION_IMMINENT, "2026-07-15T10:00:01+00:00")
    key_b = idempotency_key("codex", "s-1", COMPACTION_IMMINENT, "2026-07-15T10:00:59+00:00")

    assert key_a == key_b


def test_idempotency_key_differs_across_minutes_and_inputs() -> None:
    base = idempotency_key("codex", "s-1", COMPACTION_IMMINENT, "2026-07-15T10:00:00+00:00")

    assert base != idempotency_key("codex", "s-1", COMPACTION_IMMINENT, "2026-07-15T10:01:00+00:00")
    assert base != idempotency_key(
        "claude-code", "s-1", COMPACTION_IMMINENT, "2026-07-15T10:00:00+00:00"
    )
    assert base != idempotency_key("codex", "s-2", COMPACTION_IMMINENT, "2026-07-15T10:00:00+00:00")
    assert base != idempotency_key("codex", "s-1", "session_started", "2026-07-15T10:00:00+00:00")


def test_idempotency_key_is_metadata_only() -> None:
    # Redaction changes the payload, never the identity.
    plain = _envelope(payload={"note": "hello"})
    secret = _envelope(payload={"password": "x" * 30})

    assert plain.idempotency_key == secret.idempotency_key


# --- Serialization ---


def test_envelope_json_roundtrip() -> None:
    envelope = _envelope(payload={"trigger": "auto"})

    assert envelope_from_json(envelope_to_json(envelope)) == envelope


def test_envelope_from_json_rejects_non_object() -> None:
    # pydantic.ValidationError is a ValueError, so callers' existing handlers
    # (archive flush) catch strict-validation failures too.
    with pytest.raises(ValueError, match="should be an object"):
        envelope_from_json("[1, 2]")


def test_envelope_from_json_rejects_missing_fields() -> None:
    with pytest.raises(ValueError, match="Field required"):
        envelope_from_json(json.dumps({"id": "x"}))


def test_envelope_from_json_rejects_unknown_fields() -> None:
    data = json.loads(envelope_to_json(_envelope()))
    data["surprise"] = 1

    with pytest.raises(ValueError, match="surprise"):
        envelope_from_json(json.dumps(data))


def test_envelope_from_json_rejects_non_dict_payload() -> None:
    data = json.loads(envelope_to_json(_envelope()))
    data["payload"] = "oops"

    with pytest.raises(ValueError, match="payload"):
        envelope_from_json(json.dumps(data))


def test_envelope_from_json_rejects_future_version() -> None:
    data = json.loads(envelope_to_json(_envelope()))
    data["envelope_version"] = 2

    with pytest.raises(ValueError, match="unsupported envelope_version"):
        envelope_from_json(json.dumps(data))


def test_envelope_from_json_rejects_non_string_session_id() -> None:
    # A corrupt file can have every key present with the wrong scalar type;
    # strict mode rejects the list instead of letting flush crash grouping on it.
    data = json.loads(envelope_to_json(_envelope()))
    data["source_session_id"] = []

    with pytest.raises(ValueError, match="source_session_id"):
        envelope_from_json(json.dumps(data))


def test_envelope_from_json_rejects_non_string_project_hint() -> None:
    data = json.loads(envelope_to_json(_envelope()))
    data["project_hint"] = 1

    with pytest.raises(ValueError, match="project_hint"):
        envelope_from_json(json.dumps(data))


def test_envelope_from_json_rejects_non_string_optional_field() -> None:
    data = json.loads(envelope_to_json(_envelope()))
    data["caused_by"] = 5

    with pytest.raises(ValueError, match="caused_by"):
        envelope_from_json(json.dumps(data))


def test_envelope_from_json_rejects_non_int_version() -> None:
    data = json.loads(envelope_to_json(_envelope()))
    data["envelope_version"] = "1"

    with pytest.raises(ValueError, match="envelope_version"):
        envelope_from_json(json.dumps(data))


def test_envelope_from_json_accepts_valid_optional_nulls() -> None:
    # None for optional string fields stays valid (the common case).
    data = json.loads(envelope_to_json(_envelope()))
    data["caused_by"] = None
    data["source_turn_id"] = None

    envelope = envelope_from_json(json.dumps(data))
    assert envelope.caused_by is None
