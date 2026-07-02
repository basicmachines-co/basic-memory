"""Route-facing project-delete acceptance orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing import ProjectDeleteAcceptedProject, ProjectDeleteAcceptedResult
from basic_memory.models import Project
from basic_memory.runtime import RuntimeJobId, RuntimeProjectDeleteJobRequest


class ProjectDeleteAcceptanceError(Exception):
    """Structured project-delete acceptance error for HTTP/API adapters."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ProjectDeleteJobEnqueuer(Protocol):
    """Capability that accepts background project-delete cleanup work."""

    async def enqueue_project_delete(
        self,
        request: RuntimeProjectDeleteJobRequest,
    ) -> RuntimeJobId: ...


@dataclass(frozen=True, slots=True)
class ProjectDeleteAcceptanceRequest:
    """Route-level request for accepting a project delete."""

    project_external_id: str
    delete_notes: bool


async def load_project_for_delete_acceptance(
    session: AsyncSession,
    *,
    project_external_id: str,
) -> Project | None:
    """Load the project row that the request is about to hide."""
    result = await session.execute(
        select(Project)
        .where(Project.external_id == project_external_id)
        .with_for_update(of=Project)
        .limit(1)
    )
    return result.scalars().one_or_none()


async def reactivate_accepted_project(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    project_id: int,
) -> None:
    """Undo a soft delete when the background queue rejects the request."""
    async with session_maker() as session:
        project = await session.get(Project, project_id)
        if project is None:
            return
        project.is_active = True
        await session.commit()


@dataclass(frozen=True, slots=True)
class ProjectDeleteAcceptanceService:
    """Accept project deletes quickly and leave slow cleanup to a runtime adapter."""

    session_maker: async_sessionmaker[AsyncSession]
    job_enqueuer: ProjectDeleteJobEnqueuer

    async def delete_project(
        self,
        request: ProjectDeleteAcceptanceRequest,
    ) -> ProjectDeleteAcceptedResult:
        async with self.session_maker() as session:
            project = await load_project_for_delete_acceptance(
                session,
                project_external_id=request.project_external_id,
            )
            if project is None or not project.is_active:
                raise ProjectDeleteAcceptanceError(
                    404,
                    f"Project with external_id '{request.project_external_id}' not found",
                )
            if project.is_default:
                raise ProjectDeleteAcceptanceError(
                    400,
                    f"Cannot delete default project '{project.name}'. "
                    "Set another project as default first.",
                )

            runtime_request = RuntimeProjectDeleteJobRequest(
                project_id=project.id,
                project_external_id=project.external_id,
                project_name=project.name,
                project_path=project.path,
                delete_notes=request.delete_notes,
            )
            old_project = ProjectDeleteAcceptedProject.from_source(project)
            project.is_active = False
            await session.commit()

        try:
            job_id = await self.job_enqueuer.enqueue_project_delete(runtime_request)
        except Exception:
            await reactivate_accepted_project(
                self.session_maker,
                project_id=runtime_request.project_id,
            )
            raise

        return ProjectDeleteAcceptedResult.queued(
            request=runtime_request,
            job_id=job_id,
            old_project=old_project,
        )
