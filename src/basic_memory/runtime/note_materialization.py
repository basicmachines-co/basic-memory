"""Portable note materialization handoff values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from basic_memory.runtime.contracts import (
    RuntimeFileChecksum,
    RuntimeFilePath,
    RuntimeNoteMaterializationJobRequest,
)
from basic_memory.runtime.note_object_metadata import RuntimeNoteObjectMetadata


@dataclass(frozen=True, slots=True)
class RuntimePreparedNoteWrite:
    """One optimistic note file write copied before storage I/O starts."""

    file_path: RuntimeFilePath
    markdown_content: str
    previous_file_checksum: RuntimeFileChecksum | None
    cleanup_file_path: RuntimeFilePath | None
    cleanup_file_checksum: RuntimeFileChecksum | None
    attempted_at: datetime
    object_metadata: RuntimeNoteObjectMetadata


@dataclass(frozen=True, slots=True)
class RuntimeWrittenFileState:
    """Object state returned after storage accepts a materialized note write."""

    file_path: RuntimeFilePath
    file_checksum: RuntimeFileChecksum
    file_updated_at: datetime


def plan_prepared_note_write(
    *,
    request: RuntimeNoteMaterializationJobRequest,
    file_path: RuntimeFilePath,
    markdown_content: str,
    previous_file_checksum: RuntimeFileChecksum | None,
    attempted_at: datetime,
) -> RuntimePreparedNoteWrite:
    """Build the immutable storage write snapshot for one accepted note version."""
    return RuntimePreparedNoteWrite(
        file_path=file_path,
        markdown_content=markdown_content,
        previous_file_checksum=previous_file_checksum,
        cleanup_file_path=request.cleanup_file_path,
        cleanup_file_checksum=request.cleanup_file_checksum,
        attempted_at=attempted_at,
        object_metadata=RuntimeNoteObjectMetadata(
            entity_id=request.entity_id,
            db_version=request.db_version,
            db_checksum=request.db_checksum,
            actor_user_profile_id=request.actor_user_profile_id,
            actor_kind=request.actor_kind,
            actor_name=request.actor_name,
            source=request.source,
        ),
    )
