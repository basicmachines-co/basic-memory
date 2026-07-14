"""Portable orchestration for project cleanup jobs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, Self

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.models import Entity, NoteContent, Project
from basic_memory.repository import ProjectRepository
from basic_memory.runtime.cleanup import (
    RuntimeDeleteStatus,
    RuntimeFileDeleteResult,
    RuntimeNoteFileDeleteJobRequest,
    RuntimeProjectDeleteResult,
    RuntimeProjectFileSnapshot,
    plan_note_file_delete_job_request,
)
from basic_memory.runtime.jobs import RuntimeProjectDeleteJobRequest


@dataclass(frozen=True, slots=True)
class ProjectDeletePreflightResult:
    """Project state before a background hard-delete can proceed."""

    terminal_result: RuntimeProjectDeleteResult | None = None
    file_snapshots: tuple[RuntimeProjectFileSnapshot, ...] = ()

    def __post_init__(self) -> None:
        if self.terminal_result is not None and self.file_snapshots:
            raise ValueError("terminal project delete preflight cannot carry file snapshots")

    @classmethod
    def terminal(cls, result: RuntimeProjectDeleteResult) -> Self:
        """Return a preflight result that finishes without file or project deletes."""
        return cls(terminal_result=result)

    @classmethod
    def ready(cls, file_snapshots: Sequence[RuntimeProjectFileSnapshot]) -> Self:
        """Return a preflight result ready to clean files and hard-delete the project."""
        return cls(file_snapshots=tuple(file_snapshots))


class ProjectDeletePreflightProvider(Protocol):
    """Capability that checks project state and captures cleanup file snapshots."""

    async def prepare_project_delete(
        self,
        request: RuntimeProjectDeleteJobRequest,
    ) -> ProjectDeletePreflightResult: ...


class ProjectDeleteFileDeleter(Protocol):
    """Capability that deletes one materialized file owned by project cleanup."""

    async def delete_project_file(
        self,
        request: RuntimeNoteFileDeleteJobRequest,
    ) -> RuntimeFileDeleteResult: ...


class ProjectHardDeleteOutcome(StrEnum):
    """Typed result of the guarded project hard-delete step."""

    deleted = "deleted"
    missing = "missing"
    # The project was reactivated between preflight and the hard-delete
    # transaction; the row was left untouched.
    reactivated = "reactivated"


class ProjectHardDeleter(Protocol):
    """Capability that hard-deletes the project row after file cleanup."""

    async def hard_delete_project(
        self,
        request: RuntimeProjectDeleteJobRequest,
    ) -> ProjectHardDeleteOutcome: ...


class ProjectDeleteRepository(Protocol):
    """Repository capability needed to hard-delete one project."""

    async def delete(self, session: AsyncSession, entity_id: int) -> bool: ...


class ProjectDeleteRepositories(Protocol):
    """Repository provider for project delete cleanup."""

    def project_repository(self) -> ProjectDeleteRepository: ...


@dataclass(frozen=True, slots=True)
class DefaultProjectDeleteRepositories:
    """Default repository provider for local project delete cleanup."""

    def project_repository(self) -> ProjectDeleteRepository:
        return ProjectRepository()


async def load_project_file_snapshots(
    session: AsyncSession,
    *,
    project_id: int,
) -> list[RuntimeProjectFileSnapshot]:
    """Return accepted file snapshots needed for guarded project cleanup."""
    result = await session.execute(
        select(
            Entity.id,
            Entity.file_path,
            Entity.checksum,
            NoteContent.file_checksum,
        )
        .outerjoin(NoteContent, NoteContent.entity_id == Entity.id)
        .where(Entity.project_id == project_id)
        .order_by(Entity.file_path.asc())
    )
    return [
        RuntimeProjectFileSnapshot(
            entity_id=int(row.id),
            file_path=str(row.file_path),
            # Non-markdown entities have no note_content row, so their accepted
            # state is the indexed entity checksum. Without the fallback the
            # guarded per-file delete sees checksum None, skips every non-note
            # file, and the project hard-delete strands those objects in storage.
            # Rows with neither checksum still skip (nothing safe to guard on).
            file_checksum=project_file_snapshot_checksum(
                note_file_checksum=row.file_checksum,
                entity_checksum=row.checksum,
            ),
        )
        for row in result.all()
    ]


def project_file_snapshot_checksum(
    *,
    note_file_checksum: str | None,
    entity_checksum: str | None,
) -> str | None:
    """Choose the accepted delete-guard checksum for one project file snapshot."""
    if note_file_checksum is not None:
        return str(note_file_checksum)
    if entity_checksum is not None:
        return str(entity_checksum)
    return None


@dataclass(frozen=True, slots=True)
class RepositoryProjectDeletePreflight:
    """Repository-backed preflight for one project hard-delete job."""

    session_maker: async_sessionmaker[AsyncSession]

    async def prepare_project_delete(
        self,
        request: RuntimeProjectDeleteJobRequest,
    ) -> ProjectDeletePreflightResult:
        async with db.scoped_session(self.session_maker) as session:
            project = await session.get(Project, request.project_id)
            if project is None:
                return ProjectDeletePreflightResult.terminal(
                    RuntimeProjectDeleteResult(
                        project_id=request.project_id,
                        project_external_id=request.project_external_id,
                        status=RuntimeDeleteStatus.missing,
                        deleted_project=False,
                        deleted_files=0,
                        skipped_files=0,
                        missing_files=0,
                        reason=f"project already absent: {request.project_id}",
                    )
                )

            if project.is_active:
                return ProjectDeletePreflightResult.terminal(
                    RuntimeProjectDeleteResult(
                        project_id=request.project_id,
                        project_external_id=request.project_external_id,
                        status=RuntimeDeleteStatus.skipped,
                        deleted_project=False,
                        deleted_files=0,
                        skipped_files=0,
                        missing_files=0,
                        reason=f"project is active: {request.project_id}",
                    )
                )

            file_snapshots = (
                await load_project_file_snapshots(session, project_id=request.project_id)
                if request.delete_notes
                else []
            )
            return ProjectDeletePreflightResult.ready(file_snapshots)


@dataclass(frozen=True, slots=True)
class RepositoryProjectHardDeleter:
    """Repository-backed hard deleter for one inactive project."""

    session_maker: async_sessionmaker[AsyncSession]
    repositories: ProjectDeleteRepositories = field(
        default_factory=DefaultProjectDeleteRepositories
    )

    async def hard_delete_project(
        self,
        request: RuntimeProjectDeleteJobRequest,
    ) -> ProjectHardDeleteOutcome:
        async with db.scoped_session(self.session_maker) as session:
            # Trigger: the project was reactivated while the per-file cleanup loop ran.
            # Why: preflight checked is_active once, potentially long before this
            #   transaction; hard-deleting a reactivated project destroys live data.
            # Outcome: re-verify inside the delete transaction (row-locked where the
            #   backend supports FOR UPDATE) and abort instead of deleting.
            project = await session.get(Project, request.project_id, with_for_update=True)
            if project is None:
                return ProjectHardDeleteOutcome.missing
            if project.is_active:
                logger.warning(
                    "Aborting project hard delete: project was reactivated after preflight",
                    project_id=request.project_id,
                    project_external_id=request.project_external_id,
                )
                return ProjectHardDeleteOutcome.reactivated

            deleted = await self.repositories.project_repository().delete(
                session,
                request.project_id,
            )
            return (
                ProjectHardDeleteOutcome.deleted if deleted else ProjectHardDeleteOutcome.missing
            )


async def run_project_delete(
    request: RuntimeProjectDeleteJobRequest,
    *,
    preflight: ProjectDeletePreflightProvider,
    file_deleter: ProjectDeleteFileDeleter,
    hard_deleter: ProjectHardDeleter,
) -> RuntimeProjectDeleteResult:
    """Run one project cleanup request through file cleanup then hard delete."""
    preflight_result = await preflight.prepare_project_delete(request)
    if preflight_result.terminal_result is not None:
        return preflight_result.terminal_result

    file_results: list[RuntimeFileDeleteResult] = []
    for file_snapshot in preflight_result.file_snapshots:
        file_results.append(
            await file_deleter.delete_project_file(
                plan_note_file_delete_job_request(
                    file_snapshot.to_pending_note_file_delete(project_id=request.project_id)
                )
            )
        )

    hard_delete_outcome = await hard_deleter.hard_delete_project(request)
    if hard_delete_outcome is ProjectHardDeleteOutcome.deleted:
        status = RuntimeDeleteStatus.deleted
        reason = f"project deleted: {request.project_id}"
    elif hard_delete_outcome is ProjectHardDeleteOutcome.reactivated:
        status = RuntimeDeleteStatus.skipped
        reason = f"project reactivated before hard delete: {request.project_id}"
    else:
        status = RuntimeDeleteStatus.missing
        reason = f"project already absent: {request.project_id}"

    return RuntimeProjectDeleteResult.from_file_results(
        project_id=request.project_id,
        project_external_id=request.project_external_id,
        status=status,
        deleted_project=hard_delete_outcome is ProjectHardDeleteOutcome.deleted,
        file_results=file_results,
        reason=reason,
    )
