"""Portable orchestration for guarded note-file cleanup jobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, assert_never

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.repository.note_file_vacate_repository import NoteFileVacateRepository
from basic_memory.runtime.cleanup import (
    RuntimeFileDeleteResult,
    RuntimeGuardedFileDeleteOutcome,
    RuntimeNoteFileDeleteJobRequest,
)
from basic_memory.runtime.storage import ProjectId, RuntimeFileChecksum, RuntimeFilePath


class NoteFileDeleteStorage(Protocol):
    """Capability that conditionally deletes one materialized note file."""

    async def delete_file_if_matches(
        self,
        path: RuntimeFilePath,
        *,
        expected_checksum: RuntimeFileChecksum,
    ) -> RuntimeGuardedFileDeleteOutcome:
        """Delete the object only if it is still the version ``expected_checksum`` identifies.

        The accepted checksum can come from note_content (a content checksum) or from the entity
        row, and an entity row records whatever checksum its storage indexed. Storage alone knows
        which representations identify its current object, so it owns the match
        (basic-memory-cloud#2167). The match and the delete must also close together, or a
        replacement written between them would be removed (basic-memory-cloud#1618). Backends
        without a native precondition (local filesystem) re-verify immediately before deleting;
        storage with a conditional delete (S3 If-Match) enforces it server-side.
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
        return RuntimeFileDeleteResult.no_accepted_checksum(
            entity_id=request.entity_id,
            file_path=request.file_path,
        )

    outcome = await storage.delete_file_if_matches(
        request.file_path,
        expected_checksum=request.file_checksum,
    )
    match outcome:
        case RuntimeGuardedFileDeleteOutcome.deleted:
            result = RuntimeFileDeleteResult.deleted(
                entity_id=request.entity_id,
                file_path=request.file_path,
            )
        case RuntimeGuardedFileDeleteOutcome.missing:
            result = RuntimeFileDeleteResult.already_absent(
                entity_id=request.entity_id,
                file_path=request.file_path,
            )
        case RuntimeGuardedFileDeleteOutcome.changed:
            result = RuntimeFileDeleteResult.changed_before_delete(
                entity_id=request.entity_id,
                file_path=request.file_path,
            )
        case _:
            assert_never(outcome)
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
