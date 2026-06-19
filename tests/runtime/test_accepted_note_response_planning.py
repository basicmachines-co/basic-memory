import json
from datetime import UTC, datetime
from types import SimpleNamespace

from basic_memory.runtime import (
    plan_accepted_note_response,
    runtime_note_content_payload_as_dict,
    runtime_note_content_payload_as_json_bytes,
)


def test_plan_accepted_note_response_uses_fallback_source_when_missing():
    now = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)
    entity = SimpleNamespace(
        external_id="entity-1",
        id=42,
        title="Accepted",
        note_type="note",
        content_type="text/markdown",
        permalink="accepted",
        file_path="notes/accepted.md",
        entity_metadata={"topic": "runtime"},
        created_at=now,
        updated_at=now,
        created_by="creator",
        last_updated_by="editor",
    )
    note_content = SimpleNamespace(
        markdown_content="# Accepted\n",
        db_version=4,
        db_checksum="db-checksum",
        file_version=None,
        file_checksum=None,
        file_write_status="pending",
        last_source=None,
        file_updated_at=None,
        last_materialization_error=None,
    )

    response = plan_accepted_note_response(
        entity=entity,
        note_content=note_content,
        fallback_source="api",
    )

    assert response.last_source == "api"
    assert response.to_response_payload()["last_source"] == "api"


def test_plan_accepted_note_response_prefers_note_content_source():
    now = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)
    entity = SimpleNamespace(
        external_id="entity-1",
        id=42,
        title="Accepted",
        note_type="note",
        content_type="text/markdown",
        permalink="accepted",
        file_path="notes/accepted.md",
        entity_metadata=None,
        created_at=now,
        updated_at=now,
        created_by=None,
        last_updated_by=None,
    )
    note_content = SimpleNamespace(
        markdown_content="# Accepted\n",
        db_version=4,
        db_checksum="db-checksum",
        file_version=3,
        file_checksum="file-checksum",
        file_write_status="synced",
        last_source="s3_webhook",
        file_updated_at=now,
        last_materialization_error=None,
    )

    response = plan_accepted_note_response(
        entity=entity,
        note_content=note_content,
        fallback_source="api",
    )

    assert response.last_source == "s3_webhook"
    assert response.to_response_payload()["last_source"] == "s3_webhook"


def test_runtime_note_content_payload_as_dict_serializes_accepted_response():
    now = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)
    entity = SimpleNamespace(
        external_id="entity-1",
        id=42,
        title="Accepted",
        note_type="note",
        content_type="text/markdown",
        permalink="accepted",
        file_path="notes/accepted.md",
        entity_metadata={"topic": "runtime"},
        created_at=now,
        updated_at=now,
        created_by="creator",
        last_updated_by="editor",
    )
    note_content = SimpleNamespace(
        markdown_content="# Accepted\n",
        db_version=4,
        db_checksum="db-checksum",
        file_version=None,
        file_checksum=None,
        file_write_status="pending",
        last_source=None,
        file_updated_at=None,
        last_materialization_error=None,
    )

    response = plan_accepted_note_response(
        entity=entity,
        note_content=note_content,
        fallback_source="api",
    )

    payload = runtime_note_content_payload_as_dict(response)

    assert payload == response.to_response_payload()
    assert payload["last_source"] == "api"


def test_runtime_note_content_payload_serializers_preserve_mapping_payloads():
    payload = {"deleted": False, "reason": "unchanged"}

    assert runtime_note_content_payload_as_dict(payload) == payload
    assert json.loads(runtime_note_content_payload_as_json_bytes(payload)) == payload
