"""Tests for portable runtime worker payload boundaries."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

import pytest

from basic_memory import runtime as runtime_module
from basic_memory.runtime import (
    NOTE_OBJECT_ACTOR_KIND_MCP_CLIENT,
    RuntimeJobPayloadSerializer,
    RuntimeJobPayloadSource,
    RuntimeJobRequest,
    RuntimeNoteFileDeleteJobPayload,
    RuntimeNoteFileDeleteJobRequest,
    RuntimeNoteMaterializationJobPayload,
    RuntimeNoteMaterializationJobRequest,
    RuntimePayloadJobEnqueuer,
    enqueue_runtime_job_payload,
)


@dataclass(slots=True)
class FakeJobRuntime:
    """Runtime double that records the concrete queue request it receives."""

    job_id: str = "job-1"
    requests: list[RuntimeJobRequest] = field(default_factory=list)

    async def enqueue(self, request: RuntimeJobRequest) -> str:
        self.requests.append(request)
        return self.job_id


class FakeRuntimeJobPayload:
    """Payload double that owns concrete runtime request construction."""

    def __init__(self, request: RuntimeJobRequest) -> None:
        self.request = request
        self.headers: Mapping[str, str] | None = None

    def runtime_job_request(
        self,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> RuntimeJobRequest:
        self.headers = headers
        return self.request


@dataclass(frozen=True, slots=True)
class NoteFileDeletePayloadSerializer:
    def serialize(
        self,
        request: RuntimeNoteFileDeleteJobRequest,
    ) -> RuntimeNoteFileDeleteJobPayload:
        return RuntimeNoteFileDeleteJobPayload.from_runtime_request(request)


def test_runtime_note_file_delete_job_payload_round_trips_runtime_request() -> None:
    """The Pydantic worker payload preserves the queue-neutral delete request."""
    runtime_request = RuntimeNoteFileDeleteJobRequest(
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


@pytest.mark.asyncio
async def test_runtime_payload_job_enqueuer_validates_serializes_and_queues() -> None:
    """The typed enqueuer builds the concrete job request without queue-specific code."""
    runtime_request = RuntimeNoteFileDeleteJobRequest(
        project_id=101,
        entity_id=42,
        file_path="notes/a.md",
        file_checksum="file-sum",
    )
    payload = RuntimeNoteFileDeleteJobPayload.from_runtime_request(runtime_request)
    execute_after = timedelta(seconds=5)
    runtime = FakeJobRuntime(job_id="job-42")
    payload_serializer: RuntimeJobPayloadSerializer[RuntimeNoteFileDeleteJobRequest] = (
        NoteFileDeletePayloadSerializer()
    )
    enqueuer = RuntimePayloadJobEnqueuer(
        runtime=runtime,
        entrypoint="delete_note_file",
        payload_serializer=payload_serializer,
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
                "project_id": str(runtime_request.project_id),
            },
        )
    ]


@pytest.mark.asyncio
async def test_enqueue_runtime_job_payload_uses_payload_owned_request_builder() -> None:
    """Queueable payloads keep special request semantics while adapters stay generic."""
    request = RuntimeJobRequest(
        entrypoint="custom_entrypoint",
        payload=b"{}",
        dedupe_key="custom-dedupe",
        headers={"origin": "custom"},
        execute_after=timedelta(seconds=10),
    )
    payload_source: RuntimeJobPayloadSource = FakeRuntimeJobPayload(request)
    runtime = FakeJobRuntime(job_id="job-99")

    job_id = await enqueue_runtime_job_payload(
        runtime,
        payload_source,
        headers={"source": "test"},
    )

    assert job_id == "job-99"
    assert isinstance(payload_source, FakeRuntimeJobPayload)
    assert payload_source.headers == {"source": "test"}
    assert runtime.requests == [request]


def test_runtime_note_materialization_job_payload_round_trips_runtime_request() -> None:
    """The Pydantic worker payload preserves the queue-neutral materialization request."""
    runtime_request = RuntimeNoteMaterializationJobRequest(
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
