"""Portable note materialization handoff values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from basic_memory.runtime.note_content import (
    RuntimeExpectedFileState,
    RuntimeFileChecksumReader,
    RuntimeNoteMaterializationJobRequest,
    assert_runtime_file_matches_expected,
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
    await assert_runtime_file_matches_expected(
        content_store,
        RuntimeExpectedFileState(
            file_path=prepared_write.file_path,
            expected_checksum=prepared_write.previous_file_checksum,
        ),
    )
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
