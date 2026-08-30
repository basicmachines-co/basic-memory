"""Tests for portable runtime worker payload boundaries."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from basic_memory.indexing.wiki_projector import WIKI_PROJECTOR_SOURCE
from basic_memory.runtime.cleanup import RuntimeNoteFileDeleteJobRequest
from basic_memory.runtime.job_payloads import (
    DELETE_NOTE_FILE_ENTRYPOINT,
    MATERIALIZE_NOTE_FILE_ENTRYPOINT,
    RuntimeNoteFileDeleteJobPayload,
    RuntimeNoteMaterializationJobPayload,
)
from basic_memory.runtime.jobs import RuntimeJobRequest
from basic_memory.runtime.note_content import RuntimeNoteMaterializationJobRequest
from basic_memory.runtime.note_object_metadata import NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT
from basic_memory.runtime.project_partition import (
    RuntimeAcceptedProjectNoteChange,
    RuntimeProjectNoteOperation,
)


def _project_change() -> RuntimeAcceptedProjectNoteChange:
    return RuntimeAcceptedProjectNoteChange(
        project_id=101,
        project_external_id="project-123",
        partition_position=7,
        entity_id=42,
        note_external_id="note-123",
        title="A",
        operation=RuntimeProjectNoteOperation.updated,
        file_path="notes/a.md",
        accepted_at=datetime(2026, 8, 29, 12, tzinfo=UTC),
        source="mcp",
        db_version=4,
        db_checksum="db-sum",
        actor_user_profile_id=UUID("33333333-3333-4333-8333-333333333333"),
        actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
        actor_name="Claude Code",
    )


def test_runtime_note_file_delete_job_payload_round_trips_runtime_request() -> None:
    """The Pydantic worker payload preserves the queue-neutral delete request."""
    runtime_request = RuntimeNoteFileDeleteJobRequest(
        project_id=101,
        entity_id=42,
        file_path="notes/a.md",
        file_checksum="file-sum",
        project_change=_project_change(),
    )

    payload = RuntimeNoteFileDeleteJobPayload.from_runtime_request(runtime_request)

    assert payload.to_runtime_request() == runtime_request


def test_runtime_note_file_entrypoints_export_cloud_queue_names() -> None:
    """The portable runtime contract owns note-file queue names."""
    assert DELETE_NOTE_FILE_ENTRYPOINT == "delete_note_file"
    assert MATERIALIZE_NOTE_FILE_ENTRYPOINT == "materialize_note_file"


def test_runtime_note_file_delete_job_payload_builds_runtime_queue_request() -> None:
    """Delete payloads build the concrete runtime job request shape."""
    payload = RuntimeNoteFileDeleteJobPayload(
        project_id=101,
        entity_id=42,
        file_path="notes/a.md",
        file_checksum="file-sum",
    )

    request = payload.runtime_job_request(headers={"source": "test"})

    assert request == RuntimeJobRequest(
        entrypoint="delete_note_file",
        payload=payload.model_dump_json().encode("utf-8"),
        dedupe_key="delete-note-file:101:42:notes/a.md:file-sum",
        headers={"source": "test", "project_id": "101"},
    )


def test_runtime_note_materialization_job_payload_round_trips_runtime_request() -> None:
    """The Pydantic worker payload preserves the queue-neutral materialization request."""
    runtime_request = RuntimeNoteMaterializationJobRequest(
        project_id=101,
        entity_id=42,
        db_version=4,
        db_checksum="db-sum",
        project_change=_project_change(),
        actor_user_profile_id=UUID("33333333-3333-3333-3333-333333333333"),
        actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
        actor_name="Claude Code",
        source="mcp",
        previous_file_path="notes/previous.md",
        cleanup_file_path="notes/old.md",
        cleanup_file_checksum="old-file-sum",
    )

    payload = RuntimeNoteMaterializationJobPayload.from_runtime_request(runtime_request)

    assert payload.to_runtime_request() == runtime_request


def test_runtime_note_materialization_job_payload_builds_runtime_queue_request() -> None:
    """Materialization payloads build the concrete runtime job request shape."""
    actor_user_profile_id = UUID("33333333-3333-3333-3333-333333333333")
    payload = RuntimeNoteMaterializationJobPayload(
        project_id=101,
        entity_id=42,
        db_version=4,
        db_checksum="db-sum",
        actor_user_profile_id=actor_user_profile_id,
        actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
        actor_name="Claude Code",
        source="mcp",
        cleanup_file_path="notes/old.md",
        cleanup_file_checksum="old-file-sum",
    )

    request = payload.runtime_job_request(headers={"source": "test"})

    assert request == RuntimeJobRequest(
        entrypoint="materialize_note_file",
        payload=payload.model_dump_json().encode("utf-8"),
        dedupe_key="materialize-note-file:101:42:4:db-sum",
        headers={"source": "test", "project_id": "101"},
    )


def test_runtime_note_materialization_job_payload_normalizes_origin_fields() -> None:
    """Payload validation keeps worker metadata in the runtime origin vocabulary."""
    payload = RuntimeNoteMaterializationJobPayload(
        project_id=101,
        entity_id=42,
        db_version=4,
        db_checksum="db-sum",
        actor_kind=f" {NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT} ",
        actor_name="  Claude Code  ",
        source=" mcp ",
    )

    assert payload.actor_kind == NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT
    assert payload.actor_name == "Claude Code"
    assert payload.source == "mcp"


def test_runtime_note_materialization_job_payload_accepts_wiki_projector_source() -> None:
    """Generated OKF notes preserve their projector source through materialization."""
    payload = RuntimeNoteMaterializationJobPayload(
        project_id=101,
        entity_id=42,
        db_version=4,
        db_checksum="db-sum",
        source=WIKI_PROJECTOR_SOURCE,
    )

    assert payload.source == WIKI_PROJECTOR_SOURCE


def test_runtime_note_materialization_job_payload_rejects_unknown_origin_fields() -> None:
    """Bad queued origins should fail before they become materialized file metadata."""
    with pytest.raises(ValueError, match="unsupported note materialization actor kind"):
        RuntimeNoteMaterializationJobPayload(
            project_id=101,
            entity_id=42,
            db_version=4,
            db_checksum="db-sum",
            actor_kind="agent_session",
        )
    with pytest.raises(ValueError, match="unsupported note materialization source"):
        RuntimeNoteMaterializationJobPayload(
            project_id=101,
            entity_id=42,
            db_version=4,
            db_checksum="db-sum",
            source="spoofed",
        )
