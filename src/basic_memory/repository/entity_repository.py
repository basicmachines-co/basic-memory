"""Repository for managing entities in the knowledge graph."""

from pathlib import Path
from typing import List, Optional, Sequence, Union

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.interfaces import LoaderOption
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

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
            .where(Entity.file_path == str(file_path))
            .options(*self.get_load_options())
        )
        return await self.find_one(query)

    async def delete_by_file_path(self, file_path: Union[Path, str]) -> bool:
        """Delete entity with the provided file_path.

        Args:
            file_path: Path to the entity file (will be converted to string internally)
        """
        return await self.delete_by_fields(file_path=str(file_path))

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
        """Insert or update entity using SQLite's ON CONFLICT clause.

        This method uses SQLite's native ON CONFLICT semantics to handle race conditions
        efficiently without manual exception handling. It's atomic and eliminates the need
        for separate checks and updates.

        Args:
            entity: The entity to insert or update

        Returns:
            The inserted or updated entity
        """
        async with db.scoped_session(self.session_maker) as session:
            # Set project_id if applicable and not already set
            self._set_project_id_if_needed(entity)

            # Build entity data dictionary from the entity object
            entity_data = {
                "file_path": entity.file_path,
                "project_id": entity.project_id,
                "title": entity.title,
                "entity_type": entity.entity_type,
                "entity_metadata": entity.entity_metadata,
                "content_type": entity.content_type,
                "permalink": entity.permalink,
                "checksum": entity.checksum,
                "created_at": entity.created_at,
                "updated_at": entity.updated_at,
            }

            # First attempt: Try to upsert with the given permalink
            stmt = sqlite_insert(Entity).values(entity_data)
            stmt = stmt.on_conflict_do_update(
                index_elements=["file_path"],
                set_={
                    "title": stmt.excluded.title,
                    "entity_type": stmt.excluded.entity_type,
                    "entity_metadata": stmt.excluded.entity_metadata,
                    "content_type": stmt.excluded.content_type,
                    "permalink": stmt.excluded.permalink,
                    "checksum": stmt.excluded.checksum,
                    "updated_at": stmt.excluded.updated_at,
                },
            )

            try:
                await session.execute(stmt)
                await session.flush()
            except IntegrityError as e:
                # If we get here, it's likely a permalink conflict
                if "UNIQUE constraint failed: entity.permalink" in str(e):
                    await session.rollback()
                    # Handle permalink conflict by generating a unique permalink
                    return await self._handle_permalink_conflict_optimistic(entity_data, session)
                raise

            # Retrieve the entity with relationships loaded
            query = (
                self.select()
                .where(Entity.file_path == entity.file_path)
                .options(*self.get_load_options())
            )
            result = await session.execute(query)
            found = result.scalar_one_or_none()

            if not found:  # pragma: no cover
                raise RuntimeError(f"Failed to retrieve entity after upsert: {entity.file_path}")

            return found

    async def _handle_permalink_conflict_optimistic(
        self, entity_data: dict, session: AsyncSession
    ) -> Entity:
        """Handle permalink conflicts by generating a unique permalink.

        Args:
            entity_data: Dictionary of entity data to insert
            session: Database session to use

        Returns:
            The inserted entity with a unique permalink
        """
        base_permalink = entity_data["permalink"]
        project_id = entity_data.get("project_id")
        suffix = 1

        # Find a unique permalink
        while True:
            test_permalink = f"{base_permalink}-{suffix}"
            existing = await session.execute(
                select(Entity).where(
                    Entity.permalink == test_permalink, Entity.project_id == project_id
                )
            )
            if existing.scalar_one_or_none() is None:
                # Found unique permalink
                entity_data["permalink"] = test_permalink
                break
            suffix += 1

        # Insert with unique permalink using ON CONFLICT
        stmt = sqlite_insert(Entity).values(entity_data)
        stmt = stmt.on_conflict_do_update(
            index_elements=["file_path"],
            set_={
                key: stmt.excluded[key]
                for key in entity_data.keys()
                if key not in ["file_path", "id", "project_id"]
            },
        )

        await session.execute(stmt)
        await session.flush()

        # Return the inserted entity with relationships loaded
        query = (
            self.select()
            .where(Entity.file_path == entity_data["file_path"])
            .options(*self.get_load_options())
        )
        result = await session.execute(query)
        found = result.scalar_one_or_none()
        if not found:  # pragma: no cover
            raise RuntimeError(f"Failed to retrieve entity after insert: {entity_data['file_path']}")
        return found

    async def update(self, entity_id: int, entity_data: dict | Entity) -> Optional[Entity]:
        """Update an entity using SQLite's ON CONFLICT clause for atomic updates.

        This method overrides the base repository update to use SQLite's native
        ON CONFLICT semantics, eliminating race conditions between concurrent updates.

        Args:
            entity_id: The ID of the entity to update
            entity_data: Dictionary of fields to update or an Entity object

        Returns:
            The updated entity or None if no entity exists with the given ID
        """
        async with db.scoped_session(self.session_maker) as session:
            # First get the current entity
            existing_entity = await session.execute(
                select(Entity).where(Entity.id == entity_id)
            )
            entity = existing_entity.scalar_one_or_none()

            if not entity:
                return None

            # Convert Entity object to dict if needed
            if isinstance(entity_data, Entity):
                updates = {
                    column.name: getattr(entity_data, column.name)
                    for column in Entity.__table__.columns
                    if hasattr(entity_data, column.name)
                }
            else:
                updates = entity_data

            # Build complete entity data with current values + updates
            complete_data = {
                "file_path": entity.file_path,
                "project_id": entity.project_id,
                "title": entity.title,
                "entity_type": entity.entity_type,
                "content_type": entity.content_type,
                "permalink": entity.permalink,
                "checksum": entity.checksum,
                "created_at": entity.created_at,
                "updated_at": entity.updated_at,
            }

            # Apply updates
            for key, value in updates.items():
                if key in self.valid_columns and key != "id":
                    complete_data[key] = value

            # Use ON CONFLICT to handle concurrent updates atomically
            stmt = sqlite_insert(Entity).values(complete_data)
            stmt = stmt.on_conflict_do_update(
                index_elements=["file_path"],
                set_={
                    key: stmt.excluded[key]
                    for key in complete_data.keys()
                    if key not in ["file_path", "id", "project_id", "created_at"]
                },
            )

            await session.execute(stmt)
            await session.flush()

            # Return the updated entity with relationships loaded
            query = (
                self.select()
                .where(Entity.file_path == complete_data["file_path"])
                .options(*self.get_load_options())
            )
            result = await session.execute(query)
            return result.scalar_one_or_none()

    async def update_by_file_path(self, file_path: str, updates: dict) -> Optional[Entity]:
        """Update an entity by file_path using SQLite's ON CONFLICT clause.

        This is a convenience method for updating entities when you only have the file_path.

        Args:
            file_path: The file_path of the entity to update
            updates: Dictionary of fields to update

        Returns:
            The updated entity or None if no entity exists with the given file_path
        """
        # First check if entity exists
        entity = await self.get_by_file_path(file_path)
        if not entity:
            return None

        # Use the regular update method with the entity ID
        return await self.update(entity.id, updates)
