"""Pydantic boundary models for portable runtime worker payloads."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol, Self
from uuid import UUID

from pydantic import BaseModel, field_validator

from basic_memory.runtime.contracts import (
    JobEntrypoint,
    JobRuntime,
    RuntimeJobDedupeKey,
    RuntimeJobId,
    RuntimeJobRequest,
    RuntimeJobRequestSource,
    RuntimeNoteFileDeleteJobRequest,
    RuntimeNoteMaterializationJobRequest,
    RuntimeQueuedWorkflowMetadata,
    RuntimeWorkflowBroker,
    RuntimeWorkflowProgress,
    RuntimeWorkflowTransport,
    WorkflowId,
    runtime_job_request_from_source,
)
from basic_memory.runtime.note_object_metadata import (
    VALID_NOTE_OBJECT_ACTOR_KINDS,
    VALID_NOTE_OBJECT_SOURCES,
    normalize_actor_name,
)


DELETE_NOTE_FILE_ENTRYPOINT: JobEntrypoint = "delete_note_file"
MATERIALIZE_NOTE_FILE_ENTRYPOINT: JobEntrypoint = "materialize_note_file"


class RuntimeSerializedJobPayload(Protocol):
    """Validated payload that can cross a runtime worker boundary."""

    def model_dump_json(self) -> str: ...


class RuntimeJobPayloadSource(Protocol):
    """Validated payload that owns concrete runtime job request construction."""

    def runtime_job_request(
        self,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> RuntimeJobRequest: ...


async def enqueue_runtime_job_payload(
    runtime: JobRuntime,
    payload: RuntimeJobPayloadSource,
    *,
    headers: Mapping[str, str] | None = None,
) -> RuntimeJobId:
    """Queue one validated payload through the selected runtime adapter."""
    return await runtime.enqueue(payload.runtime_job_request(headers=headers))


class RuntimeJobPayloadSerializer[RequestT: RuntimeJobRequestSource](Protocol):
    """Capability that validates and serializes a runtime job request payload."""

    def serialize(self, request: RequestT) -> RuntimeSerializedJobPayload:
        """Return a validated payload ready for queue serialization."""


@dataclass(frozen=True, slots=True)
class RuntimePayloadJobEnqueuer[RequestT: RuntimeJobRequestSource]:
    """Queue a typed runtime request after validating its serialized payload."""

    runtime: JobRuntime
    entrypoint: JobEntrypoint
    payload_serializer: RuntimeJobPayloadSerializer[RequestT]

    async def enqueue(
        self,
        request: RequestT,
        *,
        headers: Mapping[str, str] | None = None,
        priority: int = 0,
        execute_after: timedelta | None = None,
    ) -> RuntimeJobId:
        """Validate, serialize, and enqueue one runtime request."""
        payload = self.payload_serializer.serialize(request)
        return await self.runtime.enqueue(
            runtime_job_request_from_source(
                request,
                entrypoint=self.entrypoint,
                payload=payload.model_dump_json().encode("utf-8"),
                headers=headers,
                priority=priority,
                execute_after=execute_after,
            )
        )


@dataclass(frozen=True, slots=True)
class RuntimeWorkflowQueueEnvelope:
    """Queue-neutral workflow job handoff with durable metadata shape helpers."""

    workflow_id: WorkflowId
    entrypoint: JobEntrypoint
    progress: RuntimeWorkflowProgress
    job_payload: RuntimeSerializedJobPayload
    workflow_payload_metadata: Mapping[str, object] | None = None
    broker: RuntimeWorkflowBroker = "pgq"

    def queued_metadata(self) -> RuntimeQueuedWorkflowMetadata:
        """Build the durable queued workflow metadata object."""
        if self.workflow_payload_metadata is None:
            raise ValueError("workflow_payload_metadata is required for queued workflow metadata")
        return RuntimeQueuedWorkflowMetadata(
            workflow_id=self.workflow_id,
            progress=self.progress,
            payload=self.workflow_payload_metadata,
            transport=RuntimeWorkflowTransport(broker=self.broker, entrypoint=self.entrypoint),
        )

    def workflow_metadata(self) -> dict[str, object]:
        """Serialize durable workflow metadata using the established shape."""
        return self.queued_metadata().workflow_metadata()

    def queued_event_data(self, *, logical_key: str) -> dict[str, object]:
        """Serialize queued workflow event data using the established shape."""
        return self.queued_metadata().queued_event_data(logical_key=logical_key)

    def job_request(
        self,
        *,
        headers: Mapping[str, str],
        dedupe_key: RuntimeJobDedupeKey,
    ) -> RuntimeJobRequest:
        """Build the concrete queue request after payload validation."""
        return RuntimeJobRequest(
            entrypoint=self.entrypoint,
            payload=self.job_payload.model_dump_json().encode("utf-8"),
            dedupe_key=dedupe_key,
            headers=dict(headers),
        )


class RuntimeNoteFileDeleteJobPayload(BaseModel):
    """Serialized worker payload for materialized note-file cleanup."""

    tenant_id: UUID
    project_id: int
    entity_id: int
    file_path: str
    file_checksum: str | None = None

    @classmethod
    def from_runtime_request(cls, request: RuntimeNoteFileDeleteJobRequest) -> Self:
        """Validate a queue-neutral runtime request at a worker payload boundary."""
        return cls(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            entity_id=request.entity_id,
            file_path=request.file_path,
            file_checksum=request.file_checksum,
        )

    def to_runtime_request(self) -> RuntimeNoteFileDeleteJobRequest:
        """Map the validated worker payload back to the queue-neutral request."""
        return RuntimeNoteFileDeleteJobRequest(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            entity_id=self.entity_id,
            file_path=self.file_path,
            file_checksum=self.file_checksum,
        )

    def runtime_job_request(
        self,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> RuntimeJobRequest:
        """Build the concrete runtime queue request for note-file deletion."""
        return runtime_job_request_from_source(
            self.to_runtime_request(),
            entrypoint=DELETE_NOTE_FILE_ENTRYPOINT,
            payload=self.model_dump_json().encode("utf-8"),
            headers=headers,
        )


class RuntimeNoteMaterializationJobPayload(BaseModel):
    """Serialized worker payload for accepted note materialization."""

    tenant_id: UUID
    project_id: int
    entity_id: int
    db_version: int
    db_checksum: str
    actor_user_profile_id: UUID | None = None
    actor_kind: str | None = None
    actor_name: str | None = None
    source: str | None = None
    cleanup_file_path: str | None = None
    cleanup_file_checksum: str | None = None

    @field_validator("actor_kind")
    @classmethod
    def validate_actor_kind(cls, value: str | None) -> str | None:
        """Keep queued materialization actor kinds in the typed origin vocabulary."""
        if value is None:
            return None

        actor_kind = value.strip()
        if not actor_kind:
            return None
        if actor_kind not in VALID_NOTE_OBJECT_ACTOR_KINDS:
            raise ValueError(f"unsupported note materialization actor kind: {actor_kind}")
        return actor_kind

    @field_validator("actor_name")
    @classmethod
    def validate_actor_name(cls, value: str | None) -> str | None:
        """Normalize the optional user-facing actor label before it reaches storage."""
        return normalize_actor_name(value)

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str | None) -> str | None:
        """Keep queued materialization sources within the live-update vocabulary."""
        if value is None:
            return None

        source = value.strip()
        if not source:
            return None
        if source not in VALID_NOTE_OBJECT_SOURCES:
            raise ValueError(f"unsupported note materialization source: {source}")
        return source

    @classmethod
    def from_runtime_request(cls, request: RuntimeNoteMaterializationJobRequest) -> Self:
        """Validate a queue-neutral runtime request at a worker payload boundary."""
        return cls(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            entity_id=request.entity_id,
            db_version=request.db_version,
            db_checksum=request.db_checksum,
            actor_user_profile_id=request.actor_user_profile_id,
            actor_kind=request.actor_kind,
            actor_name=request.actor_name,
            source=request.source,
            cleanup_file_path=request.cleanup_file_path,
            cleanup_file_checksum=request.cleanup_file_checksum,
        )

    def to_runtime_request(self) -> RuntimeNoteMaterializationJobRequest:
        """Map the validated worker payload back to the queue-neutral request."""
        return RuntimeNoteMaterializationJobRequest(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            entity_id=self.entity_id,
            db_version=self.db_version,
            db_checksum=self.db_checksum,
            actor_user_profile_id=self.actor_user_profile_id,
            actor_kind=self.actor_kind,
            actor_name=self.actor_name,
            source=self.source,
            cleanup_file_path=self.cleanup_file_path,
            cleanup_file_checksum=self.cleanup_file_checksum,
        )

    def runtime_job_request(
        self,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> RuntimeJobRequest:
        """Build the concrete runtime queue request for note materialization."""
        return runtime_job_request_from_source(
            self.to_runtime_request(),
            entrypoint=MATERIALIZE_NOTE_FILE_ENTRYPOINT,
            payload=self.model_dump_json().encode("utf-8"),
            headers=headers,
        )
