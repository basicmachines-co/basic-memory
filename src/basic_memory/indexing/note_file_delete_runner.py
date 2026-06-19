"""Portable orchestration for guarded note-file cleanup jobs."""

from __future__ import annotations

from typing import Protocol

from basic_memory.runtime import (
    RuntimeFileDeleteResult,
    RuntimeFilePath,
    RuntimeNoteFileDeleteJobRequest,
    RuntimeFileChecksum,
    plan_note_file_delete_cleanup,
    read_runtime_file_checksum,
)


class NoteFileDeleteStorage(Protocol):
    """Capability that reads and deletes one materialized note file."""

    async def exists(self, path: RuntimeFilePath) -> bool: ...

    async def compute_checksum(self, path: RuntimeFilePath) -> RuntimeFileChecksum: ...

    async def delete_file(self, path: RuntimeFilePath) -> None: ...


async def run_note_file_delete(
    request: RuntimeNoteFileDeleteJobRequest,
    *,
    storage: NoteFileDeleteStorage,
) -> RuntimeFileDeleteResult:
    """Delete a materialized note file only when storage still matches the accepted guard."""
    if request.file_checksum is None:
        return plan_note_file_delete_cleanup(
            entity_id=request.entity_id,
            file_path=request.file_path,
            accepted_checksum=request.file_checksum,
            actual_checksum=None,
        ).result

    actual_checksum = await read_runtime_file_checksum(storage, request.file_path)
    delete_plan = plan_note_file_delete_cleanup(
        entity_id=request.entity_id,
        file_path=request.file_path,
        accepted_checksum=request.file_checksum,
        actual_checksum=actual_checksum,
    )
    if delete_plan.should_delete_file:
        await storage.delete_file(request.file_path)
    return delete_plan.result
