"""Repository for managing projects in Basic Memory."""

from pathlib import Path
from typing import Optional, Sequence, Union


from sqlalchemy import inspect as sa_inspect, select, text
from sqlalchemy.exc import NoResultFound
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
        """Get project by name (exact match).

        Args:
            name: Unique name of the project
        """
        query = self.select().where(Project.name == name)
        return await self.find_one(query)

    async def get_by_name_case_insensitive(self, name: str) -> Optional[Project]:
        """Get project by name (case-insensitive match).

        Args:
            name: Project name (case-insensitive)

        Returns:
            Project if found, None otherwise
        """
        query = self.select().where(Project.name.ilike(name))
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
        query = self.select().where(Project.path == Path(path).as_posix())
        return await self.find_one(query)

    async def get_by_id(self, project_id: int) -> Optional[Project]:
        """Get project by numeric ID.

        Args:
            project_id: Numeric project ID

        Returns:
            Project if found, None otherwise
        """
        async with db.scoped_session(self.session_maker) as session:
            return await self.select_by_id(session, project_id)

    async def get_by_external_id(self, external_id: str) -> Optional[Project]:
        """Get project by external UUID.

        Args:
            external_id: External UUID identifier

        Returns:
            Project if found, None otherwise
        """
        query = self.select().where(Project.external_id == external_id)
        return await self.find_one(query)

    async def get_default_project(self) -> Optional[Project]:
        """Get the default project (the one marked as is_default=True)."""
        query = self.select().where(Project.is_default.is_(True))
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

    async def delete(self, entity_id: int) -> bool:
        """Delete a project and its derived search rows in one transaction.

        Postgres carries an ON DELETE CASCADE FK from search_index.project_id to
        project.id, so the search rows go with the project automatically there.
        SQLite stores search_index as an FTS5 virtual table, which cannot hold
        foreign keys — without an explicit purge here the FTS rows survive as
        orphans, and a later project that reuses the same auto-increment id
        inherits the previous tenant's content. search_vector_chunks is a real
        table on both backends but only carries the FK on Postgres.

        sqlite-vec stores embeddings in a separate vec0 virtual table keyed by
        chunk rowid with no cascade, so embeddings must be purged before the
        chunk rows or `_run_vector_query` will keep returning stale vectors
        that crowd out live results.

        Each derived table — search_index, search_vector_chunks,
        search_vector_embeddings — is created lazily on first use, so any of
        them may be absent on minimal test DBs or installs without semantic
        search. Inspect the connection once and skip whichever is missing.
        """
        async with db.scoped_session(self.session_maker) as session:
            try:
                result = await session.execute(
                    select(self.Model).filter(self.primary_key == entity_id)
                )
                project = result.scalars().one()
            except NoResultFound:
                return False

            existing_tables = await session.run_sync(
                lambda sync_session: set(sa_inspect(sync_session.connection()).get_table_names())
            )

            if "search_index" in existing_tables:
                await session.execute(
                    text("DELETE FROM search_index WHERE project_id = :project_id"),
                    {"project_id": entity_id},
                )

            if "search_vector_chunks" in existing_tables:
                if "search_vector_embeddings" in existing_tables:
                    # sqlite-vec has no CASCADE — drop embeddings first while the
                    # chunk rows that name them still exist.
                    await session.execute(
                        text(
                            "DELETE FROM search_vector_embeddings WHERE rowid IN ("
                            "SELECT id FROM search_vector_chunks "
                            "WHERE project_id = :project_id)"
                        ),
                        {"project_id": entity_id},
                    )
                await session.execute(
                    text("DELETE FROM search_vector_chunks WHERE project_id = :project_id"),
                    {"project_id": entity_id},
                )

            await session.delete(project)
            return True

    async def update_path(self, project_id: int, new_path: str) -> Optional[Project]:
        """Update project path.

        Args:
            project_id: ID of the project to update
            new_path: New filesystem path for the project

        Returns:
            The updated project if found, None otherwise
        """
        async with db.scoped_session(self.session_maker) as session:
            project = await self.select_by_id(session, project_id)
            if project:
                project.path = new_path
                await session.flush()
                return project
            return None
