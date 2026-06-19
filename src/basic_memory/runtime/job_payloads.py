"""Pydantic boundary models for portable runtime worker payloads."""

from typing import Self
from uuid import UUID

from pydantic import BaseModel, field_validator

from basic_memory.runtime.contracts import (
    RuntimeNoteFileDeleteJobRequest,
    RuntimeNoteMaterializationJobRequest,
)
from basic_memory.runtime.note_object_metadata import (
    VALID_NOTE_OBJECT_ACTOR_KINDS,
    VALID_NOTE_OBJECT_SOURCES,
    normalize_actor_name,
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
