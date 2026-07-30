"""Portable orchestration for guarded note-file cleanup jobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.repository.note_file_vacate_repository import NoteFileVacateRepository
from basic_memory.runtime.cleanup import (
    RuntimeFileDeleteResult,
    RuntimeNoteFileDeleteJobRequest,
    plan_note_file_delete_cleanup,
)
from basic_memory.runtime.note_content import read_runtime_file_checksum
from basic_memory.runtime.storage import ProjectId, RuntimeFileChecksum, RuntimeFilePath


class NoteFileDeleteStorage(Protocol):
    """Capability that reads and conditionally deletes one materialized note file."""

    async def exists(self, path: RuntimeFilePath) -> bool: ...

    async def compute_checksum(self, path: RuntimeFilePath) -> RuntimeFileChecksum: ...

    async def delete_file_if_unchanged(
        self,
        path: RuntimeFilePath,
        *,
        expected_checksum: RuntimeFileChecksum,
    ) -> bool:
        """Delete the object only if it still matches ``expected_checksum``.

        Return ``True`` when the matching object was deleted, ``False`` when it no longer matched
        (a different object now occupies the path, or it vanished). Closing this compare-and-delete
        atomically is what prevents deleting a replacement written into the gap between the freshness
        read and the delete (basic-memory-cloud#1618). Backends without a native precondition
        (local filesystem) re-verify the checksum immediately before deleting; storage with a
        conditional delete (S3 If-Match) enforces it server-side.
        """


class MoveVacateClearer(Protocol):
    """Capability that clears a move-vacate marker once its source object is gone."""

    async def clear_move_vacate(
        self,
        *,
        project_id: ProjectId,
        file_path: RuntimeFilePath,
        file_checksum: RuntimeFileChecksum | None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class RepositoryMoveVacateClearer:
    """Clear the move-vacate marker for a deleted source path via the tenant DB."""

    session_maker: async_sessionmaker[AsyncSession]

    async def clear_move_vacate(
        self,
        *,
        project_id: ProjectId,
        file_path: RuntimeFilePath,
        file_checksum: RuntimeFileChecksum | None,
    ) -> None:
        async with db.scoped_session(self.session_maker) as session:
            await NoteFileVacateRepository(project_id).clear_vacate(
                session,
                file_path=file_path,
                file_checksum=file_checksum,
            )


async def run_note_file_delete(
    request: RuntimeNoteFileDeleteJobRequest,
    *,
    storage: NoteFileDeleteStorage,
    vacate_clearer: MoveVacateClearer | None = None,
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
    result = delete_plan.result
    if delete_plan.should_delete_file:
        # The freshness read above proved the object matched, but a writer can replace it in the
        # window before the delete. The delete is therefore conditional on the same checksum; if it
        # no longer matches, we deleted nothing and report `changed` rather than claiming a delete
        # of a replacement object (basic-memory-cloud#1618).
        deleted = await storage.delete_file_if_unchanged(
            request.file_path,
            expected_checksum=request.file_checksum,
        )
        if not deleted:
            result = RuntimeFileDeleteResult.changed_before_delete(
                entity_id=request.entity_id,
                file_path=request.file_path,
            )
    # Every guarded outcome proves the moved source object is no longer pending: it was deleted,
    # was already missing, or was replaced by different content. Retire only the marker for this
    # accepted checksum; a newer move that refreshed the path marker remains protected.
    if vacate_clearer is not None:
        await vacate_clearer.clear_move_vacate(
            project_id=request.project_id,
            file_path=request.file_path,
            file_checksum=request.file_checksum,
        )
    return result
