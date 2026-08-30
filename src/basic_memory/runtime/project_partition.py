"""Portable evidence for one accepted change in a strict project partition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from basic_memory.runtime.storage import (
    NoteExternalId,
    ProjectExternalId,
    ProjectId,
    RuntimeEntityId,
    RuntimeFilePath,
    RuntimeNoteActorKind,
    RuntimeNoteActorName,
    RuntimeNoteChangeSource,
    RuntimeNoteContentChecksum,
    RuntimeNoteContentVersion,
)

type ProjectPartitionPosition = int


class RuntimeProjectNoteOperation(StrEnum):
    """Accepted note operation recorded for project-wide consumers."""

    created = "created"
    updated = "updated"
    moved = "moved"
    deleted = "deleted"


@dataclass(frozen=True, slots=True)
class RuntimeAcceptedProjectNoteChange:
    """Replay-complete accepted note evidence carried to runtime follow-ups."""

    project_id: ProjectId
    project_external_id: ProjectExternalId
    partition_position: ProjectPartitionPosition
    entity_id: RuntimeEntityId
    note_external_id: NoteExternalId
    title: str
    operation: RuntimeProjectNoteOperation
    file_path: RuntimeFilePath
    accepted_at: datetime
    source: RuntimeNoteChangeSource
    previous_file_path: RuntimeFilePath | None = None
    db_version: RuntimeNoteContentVersion | None = None
    db_checksum: RuntimeNoteContentChecksum | None = None
    actor_user_profile_id: UUID | None = None
    actor_kind: RuntimeNoteActorKind | None = None
    actor_name: RuntimeNoteActorName | None = None

    def __post_init__(self) -> None:
        if self.partition_position <= 0:
            raise ValueError("Accepted project change position must be positive")
        if not self.project_external_id.strip():
            raise ValueError("Accepted project change requires project_external_id")
        if not self.note_external_id.strip():
            raise ValueError("Accepted project change requires note_external_id")
        if not self.title.strip():
            raise ValueError("Accepted project change requires title")
        if not self.file_path.strip():
            raise ValueError("Accepted project change requires file_path")
        if self.accepted_at.tzinfo is None:
            raise ValueError("Accepted project change accepted_at must be timezone-aware")
        if not self.source.strip():
            raise ValueError("Accepted project change requires source")
        if (self.db_version is None) != (self.db_checksum is None):
            raise ValueError(
                "Accepted project change revision requires both db_version and db_checksum"
            )
        if self.db_version is not None and self.db_version <= 0:
            raise ValueError("Accepted project change db_version must be positive")
        if self.db_checksum is not None and not self.db_checksum.strip():
            raise ValueError("Accepted project change db_checksum must not be empty")
        if self.operation == RuntimeProjectNoteOperation.moved and not self.previous_file_path:
            raise ValueError("Moved accepted project change requires previous_file_path")
