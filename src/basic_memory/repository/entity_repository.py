"""Repository for managing entities in the knowledge graph."""

from pathlib import Path
from typing import List, Optional, Sequence, Union

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.interfaces import LoaderOption

from basic_memory import db
from basic_memory.models.knowledge import Entity, Observation, Relation
from basic_memory.repository.repository import Repository


class EntityRepository(Repository[Entity]):
    """Repository for Entity model.

    Note: All file paths are stored as strings in the database. Convert Path objects
    to strings before passing to repository methods.
    """

    def __init__(self, session_maker: async_sessionmaker[AsyncSession], project_id: int):
        """Initialize with session maker and project_id filter.

        Args:
            session_maker: SQLAlchemy session maker
            project_id: Project ID to filter all operations by
        """
        super().__init__(session_maker, Entity, project_id=project_id)

    async def get_by_permalink(self, permalink: str) -> Optional[Entity]:
        """Get entity by permalink.

        Args:
            permalink: Unique identifier for the entity
        """
        query = self.select().where(Entity.permalink == permalink).options(*self.get_load_options())
        return await self.find_one(query)

    async def get_by_title(self, title: str) -> Sequence[Entity]:
        """Get entity by title.

        Args:
            title: Title of the entity to find
        """
        query = self.select().where(Entity.title == title).options(*self.get_load_options())
        result = await self.execute_query(query)
        return list(result.scalars().all())

    async def get_by_file_path(self, file_path: Union[Path, str]) -> Optional[Entity]:
        """Get entity by file_path.

        Args:
            file_path: Path to the entity file (will be converted to string internally)
        """
        query = (
            self.select()
            .where(Entity.file_path == Path(file_path).as_posix())
            .options(*self.get_load_options())
        )
        return await self.find_one(query)

    async def delete_by_file_path(self, file_path: Union[Path, str]) -> bool:
        """Delete entity with the provided file_path.

        Args:
            file_path: Path to the entity file (will be converted to string internally)
        """
        return await self.delete_by_fields(file_path=Path(file_path).as_posix())

    def get_load_options(self) -> List[LoaderOption]:
        """Get SQLAlchemy loader options for eager loading relationships."""
        return [
            selectinload(Entity.observations).selectinload(Observation.entity),
            # Load from_relations and both entities for each relation
            selectinload(Entity.outgoing_relations).selectinload(Relation.from_entity),
            selectinload(Entity.outgoing_relations).selectinload(Relation.to_entity),
            # Load to_relations and both entities for each relation
            selectinload(Entity.incoming_relations).selectinload(Relation.from_entity),
            selectinload(Entity.incoming_relations).selectinload(Relation.to_entity),
        ]

    async def find_by_permalinks(self, permalinks: List[str]) -> Sequence[Entity]:
        """Find multiple entities by their permalink.

        Args:
            permalinks: List of permalink strings to find
        """
        # Handle empty input explicitly
        if not permalinks:
            return []

        # Use existing select pattern
        query = (
            self.select().options(*self.get_load_options()).where(Entity.permalink.in_(permalinks))
        )

        result = await self.execute_query(query)
        return list(result.scalars().all())

    async def upsert_entity(self, entity: Entity) -> Entity:
        """Insert or update entity using simple try/catch with database-level conflict resolution.

        Handles file_path race conditions by checking for existing entity on IntegrityError.
        For permalink conflicts, generates a unique permalink with numeric suffix.

        Args:
            entity: The entity to insert or update

        Returns:
            The inserted or updated entity
        """
        async with db.scoped_session(self.session_maker) as session:
            # Set project_id if applicable and not already set
            self._set_project_id_if_needed(entity)

            # Try simple insert first
            try:
                session.add(entity)
                await session.flush()

                # Return with relationships loaded
                query = (
                    self.select()
                    .where(Entity.file_path == entity.file_path)
                    .options(*self.get_load_options())
                )
                result = await session.execute(query)
                found = result.scalar_one_or_none()
                if not found:  # pragma: no cover
                    raise RuntimeError(
                        f"Failed to retrieve entity after insert: {entity.file_path}"
                    )
                return found

            except IntegrityError as e:
                # Check if this is a FOREIGN KEY constraint failure
                error_str = str(e)
                if "FOREIGN KEY constraint failed" in error_str:
                    # Import locally to avoid circular dependency (repository -> services -> repository)
                    from basic_memory.services.exceptions import SyncFatalError

                    # Project doesn't exist in database - this is a fatal sync error
                    raise SyncFatalError(
                        f"Cannot sync file '{entity.file_path}': "
                        f"project_id={entity.project_id} does not exist in database. "
                        f"The project may have been deleted. This sync will be terminated."
                    ) from e

                await session.rollback()

                # Re-query after rollback to get a fresh, attached entity
                existing_result = await session.execute(
                    select(Entity)
                    .where(
                        Entity.file_path == entity.file_path, Entity.project_id == entity.project_id
                    )
                    .options(*self.get_load_options())
                )
                existing_entity = existing_result.scalar_one_or_none()

                if existing_entity:
                    # File path conflict - update the existing entity
                    logger.debug(
                        f"Resolving file_path conflict for {entity.file_path}, "
                        f"entity_id={existing_entity.id}, observations={len(entity.observations)}"
                    )
                    # Use merge to avoid session state conflicts
                    # Set the ID to update existing entity
                    entity.id = existing_entity.id

                    # Ensure observations reference the correct entity_id
                    for obs in entity.observations:
                        obs.entity_id = existing_entity.id
                        # Clear any existing ID to force INSERT as new observation
                        obs.id = None

                    # Merge the entity which will update the existing one
                    merged_entity = await session.merge(entity)

                    await session.commit()

                    # Re-query to get proper relationships loaded
                    final_result = await session.execute(
                        select(Entity)
                        .where(Entity.id == merged_entity.id)
                        .options(*self.get_load_options())
                    )
                    return final_result.scalar_one()

                else:
                    # No file_path conflict - must be permalink conflict
                    # Generate unique permalink and retry
                    entity = await self._handle_permalink_conflict(entity, session)
                    return entity

    async def get_distinct_directories(self) -> List[str]:
        """Extract unique directory paths from file_path column.

        Optimized method for getting directory structure without loading full entities
        or relationships. Returns a sorted list of unique directory paths.

        Returns:
            List of unique directory paths (e.g., ["notes", "notes/meetings", "specs"])
        """
        # Query only file_path column, no entity objects or relationships
        query = select(Entity.file_path).distinct()
        query = self._add_project_filter(query)

        # Execute with use_query_options=False to skip eager loading
        result = await self.execute_query(query, use_query_options=False)
        file_paths = [row for row in result.scalars().all()]

        # Parse file paths to extract unique directories
        directories = set()
        for file_path in file_paths:
            parts = [p for p in file_path.split("/") if p]
            # Add all parent directories (exclude filename which is the last part)
            for i in range(len(parts) - 1):
                dir_path = "/".join(parts[: i + 1])
                directories.add(dir_path)

        return sorted(directories)

    async def find_by_directory_prefix(self, directory_prefix: str) -> Sequence[Entity]:
        """Find entities whose file_path starts with the given directory prefix.

        Optimized method for listing directory contents without loading all entities.
        Uses SQL LIKE pattern matching to filter entities by directory path.

        Args:
            directory_prefix: Directory path prefix (e.g., "docs", "docs/guides")
                             Empty string returns all entities (root directory)

        Returns:
            Sequence of entities in the specified directory and subdirectories
        """
        # Build SQL LIKE pattern
        if directory_prefix == "" or directory_prefix == "/":
            # Root directory - return all entities
            return await self.find_all()

        # Remove leading/trailing slashes for consistency
        directory_prefix = directory_prefix.strip("/")

        # Query entities with file_path starting with prefix
        # Pattern matches "prefix/" to ensure we get files IN the directory,
        # not just files whose names start with the prefix
        pattern = f"{directory_prefix}/%"

        query = self.select().where(Entity.file_path.like(pattern))

        # Skip eager loading - we only need basic entity fields for directory trees
        result = await self.execute_query(query, use_query_options=False)
        return list(result.scalars().all())

    async def _handle_permalink_conflict(self, entity: Entity, session: AsyncSession) -> Entity:
        """Handle permalink conflicts by generating a unique permalink."""
        base_permalink = entity.permalink
        suffix = 1

        # Find a unique permalink
        while True:
            test_permalink = f"{base_permalink}-{suffix}"
            existing = await session.execute(
                select(Entity).where(
                    Entity.permalink == test_permalink, Entity.project_id == entity.project_id
                )
            )
            if existing.scalar_one_or_none() is None:
                # Found unique permalink
                entity.permalink = test_permalink
                break
            suffix += 1

        # Insert with unique permalink
        session.add(entity)
        await session.flush()
        return entity
