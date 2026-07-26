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
    """Capability that reads and deletes one materialized note file."""

    async def exists(self, path: RuntimeFilePath) -> bool: ...

    async def compute_checksum(self, path: RuntimeFilePath) -> RuntimeFileChecksum: ...

    async def delete_file(self, path: RuntimeFilePath) -> None: ...


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
    if delete_plan.should_delete_file:
        await storage.delete_file(request.file_path)
    # Every guarded outcome proves the moved source object is no longer pending: it was deleted,
    # was already missing, or was replaced by different content. Retire only the marker for this
    # accepted checksum; a newer move that refreshed the path marker remains protected.
    if vacate_clearer is not None:
        await vacate_clearer.clear_move_vacate(
            project_id=request.project_id,
            file_path=request.file_path,
            file_checksum=request.file_checksum,
        )
    return delete_plan.result
