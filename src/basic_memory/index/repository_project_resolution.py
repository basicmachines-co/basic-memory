"""Repository-backed storage-event project resolution."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.index.storage_events import StorageEventProjectResolver
from basic_memory.models import Project
from basic_memory.repository import ProjectRepository
from basic_memory.runtime.projects import ProjectRuntimeReference
from basic_memory.runtime.storage import ProjectPath
from basic_memory.runtime.storage_project_resolution import (
    StorageProjectPrefixMatch,
    resolve_storage_project_prefix,
)


class StorageEventProjectResolutionLogger(Protocol):
    """Logger surface used by repository-backed storage-event project resolution."""

    def info(self, message: str, /, *args: object, **kwargs: object) -> None: ...

    def warning(self, message: str, /, *args: object, **kwargs: object) -> None: ...


@dataclass(frozen=True, slots=True)
class RepositoryStorageEventProjectResolver(StorageEventProjectResolver):
    """Resolve storage-event project prefixes using the Basic Memory project repository."""

    project_repository: ProjectRepository
    session_maker: async_sessionmaker[AsyncSession]
    resolution_logger: StorageEventProjectResolutionLogger

    async def resolve_project(self, project_path: ProjectPath) -> ProjectRuntimeReference | None:
        project = await self.find_project_by_bucket_prefix(project_path)
        if project is None:
            self.resolution_logger.warning(
                f"Project not found or inactive for bucket prefix: {project_path}",
                bucket_prefix=project_path,
            )
            return None

        project_ref = ProjectRuntimeReference.from_project(project)
        project_ref.require_project_name()
        return project_ref

    async def find_project_by_bucket_prefix(
        self,
        bucket_prefix: ProjectPath,
    ) -> Project | None:
        """Find an active project that matches the storage object-key project prefix."""
        async with db.scoped_session(self.session_maker) as session:
            path_project = await self.project_repository.get_by_path(session, bucket_prefix)
            name_project = await self.project_repository.get_by_name(session, bucket_prefix)
            active_projects = await self.project_repository.get_active_projects(session)

        resolution = resolve_storage_project_prefix(
            bucket_prefix,
            exact_path_project=path_project,
            name_project=name_project,
            active_projects=active_projects,
        )
        if resolution.match == StorageProjectPrefixMatch.path_suffix:
            project = resolution.project
            if project is None:
                raise RuntimeError("Storage prefix suffix resolution had no project")
            self.resolution_logger.info(
                f"Matched project by path suffix: {project.name} (path={project.path})",
                project_name=project.name,
                project_path=project.path,
                bucket_prefix=bucket_prefix,
            )
            return project
        if resolution.matched:
            return resolution.project
        if resolution.match == StorageProjectPrefixMatch.ambiguous_path_suffix:
            self.resolution_logger.warning(
                f"Ambiguous project path suffix for bucket prefix: {bucket_prefix}",
                bucket_prefix=bucket_prefix,
                matches=[
                    {"name": project.name, "path": project.path, "id": project.id}
                    for project in resolution.suffix_matches
                ],
            )
            return None

        self.resolution_logger.info(
            f"No project found for bucket prefix: {bucket_prefix}",
            bucket_prefix=bucket_prefix,
            available_projects=[
                {"name": project.name, "path": project.path, "id": project.id}
                for project in resolution.available_projects
            ],
        )
        return None
