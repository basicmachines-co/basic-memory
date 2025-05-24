"""Repository for managing projects in Basic Memory."""

from pathlib import Path
from typing import Optional, Sequence, Union

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.models.project import Project
from basic_memory.repository.repository import Repository


class ProjectRepository(Repository[Project]):
    """Repository for Project model.

    Projects represent collections of knowledge entities grouped together.
    Each entity, observation, and relation belongs to a specific project.
    """

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]):
        """Initialize with session maker."""
        super().__init__(session_maker, Project)

    async def get_by_name(self, name: str) -> Optional[Project]:
        """Get project by name.

        Args:
            name: Unique name of the project
        """
        query = self.select().where(Project.name == name)
        return await self.find_one(query)

    async def get_by_permalink(self, permalink: str) -> Optional[Project]:
        """Get project by permalink.

        Args:
            permalink: URL-friendly identifier for the project
        """
        query = self.select().where(Project.permalink == permalink)
        return await self.find_one(query)

    async def get_by_path(self, path: Union[Path, str]) -> Optional[Project]:
        """Get project by filesystem path.

        Args:
            path: Path to the project directory (will be converted to string internally)
        """
        query = self.select().where(Project.path == str(path))
        return await self.find_one(query)

    async def get_default_project(self) -> Optional[Project]:
        """Get the default project (the one marked as is_default=True)."""
        query = self.select().where(Project.is_default.is_not(None))
        return await self.find_one(query)

    async def get_active_projects(self) -> Sequence[Project]:
        """Get all active projects."""
        query = self.select().where(Project.is_active == True)  # noqa: E712
        result = await self.execute_query(query)
        return list(result.scalars().all())

    async def set_as_default(self, project_id: int) -> Optional[Project]:
        """Set a project as the default and unset previous default.

        Args:
            project_id: ID of the project to set as default

        Returns:
            The updated project if found, None otherwise
        """
        async with db.scoped_session(self.session_maker) as session:
            # First, clear the default flag for all projects using direct SQL
            await session.execute(
                text("UPDATE project SET is_default = NULL WHERE is_default IS NOT NULL")
            )
            await session.flush()

            # Set the new default project
            target_project = await self.select_by_id(session, project_id)
            if target_project:
                target_project.is_default = True
                await session.flush()
                return target_project
            return None  # pragma: no cover
