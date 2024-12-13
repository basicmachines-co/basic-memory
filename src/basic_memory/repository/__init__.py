"""Base repository implementation."""
from typing import Type, Optional, Any, Sequence, TypeVar
from sqlalchemy import select, func, Select, Executable, inspect, Result, Column, insert
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped
from loguru import logger

from basic_memory.models import Base, Entity

T = TypeVar('T', bound=Base)

class Repository[T: Base]:
    """Base repository implementation with generic CRUD operations."""

    def __init__(self, session: AsyncSession, Model: Type[T]):
        self.session = session
        self.Model = Model
        self.mapper = inspect(self.Model).mapper
        self.primary_key: Column[Any] = self.mapper.primary_key[0]
        self.valid_columns = [column.key for column in self.mapper.columns]

        logger.debug(f"Initialized {self.__class__.__name__} for {Model.__name__}")
        logger.debug(f"Valid columns: {self.valid_columns}")

    async def refresh(self, instance: T, relationships: list[str] | None = None) -> None:
        """Refresh instance and optionally specified relationships."""
        logger.debug(f"Refreshing {self.Model.__name__} instance: {getattr(instance, 'id', None)}")
        try:
            await self.session.refresh(instance, relationships or [])
            logger.debug(f"Refreshed relationships: {relationships}")
        except Exception as e:
            logger.exception(f"Failed to refresh {self.Model.__name__} instance")
            raise

    async def find_all(self, skip: int = 0, limit: int = 100) -> Sequence[T]:
        """Fetch records from the database with pagination."""
        logger.debug(f"Finding all {self.Model.__name__} (skip={skip}, limit={limit})")
        try:
            result = await self.session.execute(
                select(self.Model).offset(skip).limit(limit)
            )
            items = result.scalars().all()
            logger.debug(f"Found {len(items)} {self.Model.__name__} records")
            return items
        except Exception as e:
            logger.exception(f"Failed to find all {self.Model.__name__}")
            raise

    async def find_by_id(self, entity_id: str) -> Optional[T]:
        """Fetch an entity by its unique identifier."""
        logger.debug(f"Finding {self.Model.__name__} by ID: {entity_id}")
        try:
            result = await self.session.execute(
                select(self.Model).filter(self.primary_key == entity_id)
            )
            entity = result.scalars().one()
            logger.debug(f"Found {self.Model.__name__}: {entity_id}")
            return entity
        except NoResultFound:
            logger.debug(f"No {self.Model.__name__} found with ID: {entity_id}")
            return None
        except Exception as e:
            logger.exception(f"Failed to find {self.Model.__name__} by ID: {entity_id}")
            raise

    async def create(self, entity_data: dict, model: Type[Base] | None = None) -> T:
        """Create a new entity in the database from the provided data."""
        model = model or self.Model
        logger.debug(f"Creating {model.__name__} with data: {entity_data}")
        try:

            # Create insert statement with only provided data
            instance = model(**entity_data)
            self.session.add(instance)
            await self.session.flush()
            logger.debug(f"Created {model.__name__}: {getattr(instance, 'id', None)}")
            return instance

        except Exception as e:
            logger.exception(f"Failed to create {model.__name__}")
            raise

    async def update(self, entity_id: str, entity_data: dict) -> Optional[T]:
        """Update an entity with the given data."""
        logger.debug(f"Updating {self.Model.__name__} {entity_id} with data: {entity_data}")
        try:
            result = await self.session.execute(
                select(self.Model).filter(self.primary_key == entity_id)
            )
            entity = result.scalars().one()
            
            for key, value in entity_data.items():
                if key in self.valid_columns:
                    setattr(entity, key, value)
            await self.session.flush()
            
            logger.debug(f"Updated {self.Model.__name__}: {entity_id}")
            return entity
        except NoResultFound:
            logger.debug(f"No {self.Model.__name__} found to update: {entity_id}")
            return None
        except Exception as e:
            logger.exception(f"Failed to update {self.Model.__name__}: {entity_id}")
            raise

    async def delete(self, entity_id: str) -> bool:
        """Delete an entity from the database."""
        logger.debug(f"Deleting {self.Model.__name__}: {entity_id}")
        try:
            result = await self.session.execute(
                select(self.Model).filter(self.primary_key == entity_id)
            )
            entity = result.scalars().one()
            await self.session.delete(entity)
            await self.session.flush()
            
            logger.debug(f"Deleted {self.Model.__name__}: {entity_id}")
            return True
        except NoResultFound:
            logger.debug(f"No {self.Model.__name__} found to delete: {entity_id}")
            return False
        except Exception as e:
            logger.exception(f"Failed to delete {self.Model.__name__}: {entity_id}")
            raise

    async def count(self, query: Executable | None = None) -> int:
        """Count entities in the database table."""
        try:
            if query is None:
                query = select(func.count()).select_from(self.Model)
            result = await self.session.execute(query)
            scalar = result.scalar()
            count = scalar if scalar is not None else 0
            
            logger.debug(f"Counted {count} {self.Model.__name__} records")
            return count
        except Exception as e:
            logger.exception(f"Failed to count {self.Model.__name__}")
            raise

    async def execute_query(self, query: Executable) -> Result[Any]:
        """Execute a query asynchronously."""
        logger.debug(f"Executing query: {query}")
        try:
            result = await self.session.execute(query)
            logger.debug("Query executed successfully")
            return result
        except Exception as e:
            logger.exception("Failed to execute query")
            raise

    async def find_one(self, query: Select[tuple[T]]) -> Optional[T]:
        """Execute a query and retrieve a single record."""
        logger.debug(f"Finding one {self.Model.__name__} with query: {query}")
        try:
            result = await self.execute_query(query)
            entity = result.scalars().one_or_none()
            if entity:
                logger.debug(f"Found {self.Model.__name__}: {getattr(entity, 'id', None)}")
            else:
                logger.debug(f"No {self.Model.__name__} found")
            return entity
        except Exception as e:
            logger.exception(f"Failed to find one {self.Model.__name__}")
            raise