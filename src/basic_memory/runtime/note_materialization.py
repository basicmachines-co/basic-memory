"""Portable note materialization handoff values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from basic_memory.file_utils import compute_checksum
from basic_memory.runtime.note_content import (
    RuntimeFileChecksumReader,
    RuntimeFileConflictError,
    RuntimeNoteMaterializationJobRequest,
    read_runtime_file_checksum,
    runtime_file_conflict,
)
from basic_memory.runtime.storage import RuntimeFileChecksum, RuntimeFilePath
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


class RuntimeFileMetadataSource(Protocol):
    """Minimal metadata returned after a runtime content-store write."""

    @property
    def modified_at(self) -> datetime: ...


class RuntimeNoteContentStore(RuntimeFileChecksumReader, Protocol):
    """Storage capability needed to materialize one accepted note file."""

    async def write_file(
        self,
        path: RuntimeFilePath,
        content: str,
        *,
        metadata: dict[str, str] | None = None,
    ) -> RuntimeFileChecksum: ...

    async def get_file_metadata(self, path: RuntimeFilePath) -> RuntimeFileMetadataSource: ...


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


async def write_prepared_note_to_content_store(
    content_store: RuntimeNoteContentStore,
    prepared_write: RuntimePreparedNoteWrite,
) -> RuntimeWrittenFileState:
    """Write one prepared accepted note after checking the expected file state."""
    accepted_checksum = await compute_checksum(prepared_write.markdown_content)
    actual_checksum = await read_runtime_file_checksum(content_store, prepared_write.file_path)

    # Trigger: the correct accepted content is already on disk — e.g. a crash
    #   after the file write but before the DB publish left the note_content row
    #   'writing', and startup recovery re-drives the same write.
    # Why: the previous_file_checksum guard would otherwise misread that
    #   already-correct file as an external conflict (previous_file_checksum is
    #   None for a new note, or the pre-write checksum for an update), stranding
    #   the row in 'external_change_detected' and never advancing file_version.
    # Outcome: skip the redundant write and return the existing file's state so
    #   the publisher advances file_version and marks the row 'synced'.
    if actual_checksum is not None and actual_checksum == accepted_checksum:
        file_metadata = await content_store.get_file_metadata(prepared_write.file_path)
        return RuntimeWrittenFileState(
            file_path=prepared_write.file_path,
            file_checksum=actual_checksum,
            file_updated_at=file_metadata.modified_at,
        )

    # A present file that matches neither the accepted content nor the expected
    # previous checksum is a genuine external edit: refuse to overwrite it.
    conflict = runtime_file_conflict(
        actual_checksum, prepared_write.previous_file_checksum, prepared_write.file_path
    )
    if conflict is not None:
        raise RuntimeFileConflictError(conflict)

    file_checksum = await content_store.write_file(
        prepared_write.file_path,
        prepared_write.markdown_content,
        metadata=prepared_write.object_metadata.to_storage_metadata(),
    )
    file_metadata = await content_store.get_file_metadata(prepared_write.file_path)
    return RuntimeWrittenFileState(
        file_path=prepared_write.file_path,
        file_checksum=file_checksum,
        file_updated_at=file_metadata.modified_at,
    )
