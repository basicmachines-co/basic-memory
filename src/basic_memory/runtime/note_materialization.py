"""Portable note materialization handoff values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from basic_memory.runtime.contracts import RuntimeFileChecksum, RuntimeFilePath
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
