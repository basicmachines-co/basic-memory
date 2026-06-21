"""Portable orchestration for project cleanup jobs."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Protocol, Self

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.models import Entity, NoteContent, Project
from basic_memory.repository import ProjectRepository
from basic_memory.runtime import (
    RuntimeDeleteStatus,
    RuntimeFileDeleteResult,
    RuntimeNoteFileDeleteJobRequest,
    RuntimeProjectDeleteJobRequest,
    RuntimeProjectDeleteResult,
    RuntimeProjectFileSnapshot,
    plan_note_file_delete_job_request,
)

type ProjectDeleteSessionScope = Callable[
    [async_sessionmaker[AsyncSession]],
    AbstractAsyncContextManager[AsyncSession],
]


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


class ProjectHardDeleter(Protocol):
    """Capability that hard-deletes the project row after file cleanup."""

    async def hard_delete_project(self, request: RuntimeProjectDeleteJobRequest) -> bool: ...


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
            file_checksum=str(row.file_checksum) if row.file_checksum is not None else None,
        )
        for row in result.all()
    ]


@dataclass(frozen=True, slots=True)
class RepositoryProjectDeletePreflight:
    """Repository-backed preflight for one project hard-delete job."""

    session_maker: async_sessionmaker[AsyncSession]
    session_scope: ProjectDeleteSessionScope = db.scoped_session

    async def prepare_project_delete(
        self,
        request: RuntimeProjectDeleteJobRequest,
    ) -> ProjectDeletePreflightResult:
        async with self.session_scope(self.session_maker) as session:
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
    session_scope: ProjectDeleteSessionScope = db.scoped_session
    repositories: ProjectDeleteRepositories = field(
        default_factory=DefaultProjectDeleteRepositories
    )

    async def hard_delete_project(self, request: RuntimeProjectDeleteJobRequest) -> bool:
        async with self.session_scope(self.session_maker) as session:
            return await self.repositories.project_repository().delete(
                session,
                request.project_id,
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
                    tenant_id=request.tenant_id,
                    file_delete=file_snapshot.to_pending_note_file_delete(
                        project_id=request.project_id
                    ),
                )
            )
        )

    deleted_project = await hard_deleter.hard_delete_project(request)
    if deleted_project:
        status = RuntimeDeleteStatus.deleted
        reason = f"project deleted: {request.project_id}"
    else:
        status = RuntimeDeleteStatus.missing
        reason = f"project already absent: {request.project_id}"

    return RuntimeProjectDeleteResult.from_file_results(
        project_id=request.project_id,
        project_external_id=request.project_external_id,
        status=status,
        deleted_project=deleted_project,
        file_results=file_results,
        reason=reason,
    )
