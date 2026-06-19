"""Pydantic boundary models for portable runtime worker payloads."""

from typing import Self
from uuid import UUID

from pydantic import BaseModel

from basic_memory.runtime.contracts import RuntimeNoteFileDeleteJobRequest


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
