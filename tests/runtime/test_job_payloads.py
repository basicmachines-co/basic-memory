"""Tests for portable runtime worker payload boundaries."""

from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

import pytest
from pydantic import BaseModel

from basic_memory import runtime as runtime_module
from basic_memory.runtime import (
    NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
    RuntimeJobRequest,
    RuntimeNoteFileDeleteJobPayload,
    RuntimeNoteFileDeleteJobRequest,
    RuntimeNoteMaterializationJobPayload,
    RuntimeNoteMaterializationJobRequest,
    RuntimePayloadJobEnqueuer,
    RuntimeWorkflowQueueEnvelope,
)


@dataclass(slots=True)
class FakeJobRuntime:
    """Runtime double that records the concrete queue request it receives."""

    job_id: str = "job-1"
    requests: list[RuntimeJobRequest] = field(default_factory=list)

    async def enqueue(self, request: RuntimeJobRequest) -> str:
        self.requests.append(request)
        return self.job_id


class FakeWorkflowPayload(BaseModel):
    """Validated payload shape used by the portable workflow queue envelope."""

    workflow_id: UUID
    tenant_id: UUID


def test_runtime_note_file_delete_job_payload_round_trips_runtime_request() -> None:
    """The Pydantic worker payload preserves the queue-neutral delete request."""
    runtime_request = RuntimeNoteFileDeleteJobRequest(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        project_id=101,
        entity_id=42,
        file_path="notes/a.md",
        file_checksum="file-sum",
    )

    payload = RuntimeNoteFileDeleteJobPayload.from_runtime_request(runtime_request)

    assert payload.to_runtime_request() == runtime_request


def test_runtime_note_file_entrypoints_export_cloud_queue_names() -> None:
    """The portable runtime contract owns note-file queue names."""
    assert runtime_module.DELETE_NOTE_FILE_ENTRYPOINT == "delete_note_file"
    assert runtime_module.MATERIALIZE_NOTE_FILE_ENTRYPOINT == "materialize_note_file"


def test_runtime_note_file_delete_job_payload_builds_runtime_queue_request() -> None:
    """Delete payloads build the concrete runtime job request shape."""
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    payload = RuntimeNoteFileDeleteJobPayload(
        tenant_id=tenant_id,
        project_id=101,
        entity_id=42,
        file_path="notes/a.md",
        file_checksum="file-sum",
    )

    request = payload.runtime_job_request(headers={"source": "test"})

    assert request == RuntimeJobRequest(
        entrypoint="delete_note_file",
        payload=payload.model_dump_json().encode("utf-8"),
        dedupe_key=(
            "delete-note-file:11111111-1111-1111-1111-111111111111:101:42:notes/a.md:file-sum"
        ),
        headers={"source": "test", "tenant_id": str(tenant_id), "project_id": "101"},
    )


@pytest.mark.asyncio
async def test_runtime_payload_job_enqueuer_validates_serializes_and_queues() -> None:
    """The typed enqueuer builds the concrete job request without queue-specific code."""
    runtime_request = RuntimeNoteFileDeleteJobRequest(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        project_id=101,
        entity_id=42,
        file_path="notes/a.md",
        file_checksum="file-sum",
    )
    payload = RuntimeNoteFileDeleteJobPayload.from_runtime_request(runtime_request)
    execute_after = timedelta(seconds=5)
    runtime = FakeJobRuntime(job_id="job-42")
    enqueuer = RuntimePayloadJobEnqueuer(
        runtime=runtime,
        entrypoint="delete_note_file",
        payload_factory=RuntimeNoteFileDeleteJobPayload.from_runtime_request,
    )

    job_id = await enqueuer.enqueue(
        runtime_request,
        headers={"source": "test"},
        priority=3,
        execute_after=execute_after,
    )

    assert job_id == "job-42"
    assert runtime.requests == [
        RuntimeJobRequest(
            entrypoint="delete_note_file",
            payload=payload.model_dump_json().encode("utf-8"),
            priority=3,
            execute_after=execute_after,
            dedupe_key=runtime_request.dedupe_key(),
            headers={
                "source": "test",
                "tenant_id": str(runtime_request.tenant_id),
                "project_id": str(runtime_request.project_id),
            },
        )
    ]


def test_runtime_workflow_queue_envelope_builds_metadata_and_job_request() -> None:
    """Workflow queue envelopes preserve the existing durable and queue shapes."""
    workflow_id = UUID("22222222-2222-2222-2222-222222222222")
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    payload = FakeWorkflowPayload(workflow_id=workflow_id, tenant_id=tenant_id)
    metadata = payload.model_dump(mode="json")
    headers = {
        "tenant_id": str(tenant_id),
        "workflow_id": str(workflow_id),
    }
    envelope = RuntimeWorkflowQueueEnvelope(
        workflow_id=workflow_id,
        entrypoint="provision_tenant",
        progress="waiting",
        job_payload=payload,
        workflow_payload_metadata=metadata,
    )

    assert envelope.workflow_metadata() == {
        "job_id": str(workflow_id),
        "phase": "queued",
        "progress": "waiting",
        "payload": metadata,
        "transport": {
            "broker": "pgq",
            "entrypoint": "provision_tenant",
        },
    }
    assert envelope.queued_event_data(logical_key="tenant:demo") == {
        "logical_key": "tenant:demo",
        "entrypoint": "provision_tenant",
        "phase": "queued",
        "progress": "waiting",
        **metadata,
    }
    assert envelope.job_request(
        headers=headers,
        dedupe_key=f"tenant:{tenant_id}",
    ) == RuntimeJobRequest(
        entrypoint="provision_tenant",
        payload=payload.model_dump_json().encode("utf-8"),
        dedupe_key=f"tenant:{tenant_id}",
        headers=headers,
    )


def test_runtime_note_materialization_job_payload_round_trips_runtime_request() -> None:
    """The Pydantic worker payload preserves the queue-neutral materialization request."""
    runtime_request = RuntimeNoteMaterializationJobRequest(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        project_id=101,
        entity_id=42,
        db_version=4,
        db_checksum="db-sum",
        actor_user_profile_id=UUID("33333333-3333-3333-3333-333333333333"),
        actor_kind=NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
        actor_name="Claude Code",
        source="mcp",
        cleanup_file_path="notes/old.md",
        cleanup_file_checksum="old-file-sum",
    )

    payload = RuntimeNoteMaterializationJobPayload.from_runtime_request(runtime_request)

    assert payload.to_runtime_request() == runtime_request


def test_runtime_note_materialization_job_payload_builds_runtime_queue_request() -> None:
    """Materialization payloads build the concrete runtime job request shape."""
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    actor_user_profile_id = UUID("33333333-3333-3333-3333-333333333333")
    payload = RuntimeNoteMaterializationJobPayload(
        tenant_id=tenant_id,
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
        dedupe_key=("materialize-note-file:11111111-1111-1111-1111-111111111111:101:42:4:db-sum"),
        headers={"source": "test", "tenant_id": str(tenant_id), "project_id": "101"},
    )


def test_runtime_note_materialization_job_payload_normalizes_origin_fields() -> None:
    """Payload validation keeps worker metadata in the runtime origin vocabulary."""
    payload = RuntimeNoteMaterializationJobPayload(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
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


def test_runtime_note_materialization_job_payload_rejects_unknown_origin_fields() -> None:
    """Bad queued origins should fail before they become materialized file metadata."""
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")

    with pytest.raises(ValueError, match="unsupported note materialization actor kind"):
        RuntimeNoteMaterializationJobPayload(
            tenant_id=tenant_id,
            project_id=101,
            entity_id=42,
            db_version=4,
            db_checksum="db-sum",
            actor_kind="agent_session",
        )
    with pytest.raises(ValueError, match="unsupported note materialization source"):
        RuntimeNoteMaterializationJobPayload(
            tenant_id=tenant_id,
            project_id=101,
            entity_id=42,
            db_version=4,
            db_checksum="db-sum",
            source="spoofed",
        )
