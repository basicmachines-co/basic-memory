"""Tests for portable runtime worker payload boundaries."""

from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

import pytest

from basic_memory.runtime import (
    NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
    RuntimeJobRequest,
    RuntimeNoteFileDeleteJobPayload,
    RuntimeNoteFileDeleteJobRequest,
    RuntimeNoteMaterializationJobPayload,
    RuntimeNoteMaterializationJobRequest,
    RuntimePayloadJobEnqueuer,
)


@dataclass(slots=True)
class FakeJobRuntime:
    """Runtime double that records the concrete queue request it receives."""

    job_id: str = "job-1"
    requests: list[RuntimeJobRequest] = field(default_factory=list)

    async def enqueue(self, request: RuntimeJobRequest) -> str:
        self.requests.append(request)
        return self.job_id


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
