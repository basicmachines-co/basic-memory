"""Base repository implementation."""

from typing import Type, Optional, Any, Sequence, TypeVar, List

from loguru import logger
from sqlalchemy import (
    select,
    func,
    Select,
    Executable,
    inspect,
    Result,
    Column,
    and_,
    delete,
)
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from sqlalchemy.orm.interfaces import LoaderOption

from basic_memory import db
from basic_memory.models import Base

T = TypeVar("T", bound=Base)


class Repository[T: Base]:
    """Base repository implementation with generic CRUD operations."""

    def __init__(self, session_maker: async_sessionmaker[AsyncSession], Model: Type[T]):
        self.session_maker = session_maker
        if Model:
            self.Model = Model
            self.mapper = inspect(self.Model).mapper
            self.primary_key: Column[Any] = self.mapper.primary_key[0]
            self.valid_columns = [column.key for column in self.mapper.columns]

    def get_model_data(self, entity_data):
        model_data = {
            k: v for k, v in entity_data.items() if k in self.valid_columns and v is not None
        }
        return model_data

    async def select_by_id(self, session: AsyncSession, entity_id: int) -> Optional[T]:
        """Select an entity by ID using an existing session."""
        query = (
            select(self.Model)
            .filter(self.primary_key == entity_id)
            .options(*self.get_load_options())
        )
        result = await session.execute(query)
        return result.scalars().one_or_none()

    async def select_by_ids(self, session: AsyncSession, ids: List[int]) -> Sequence[T]:
        """Select multiple entities by IDs using an existing session."""
        query = (
            select(self.Model).where(self.primary_key.in_(ids)).options(*self.get_load_options())
        )
        result = await session.execute(query)
        return result.scalars().all()

    async def add(self, model: T) -> T:
        """
        Add a model to the repository. This will also add related objects
        :param model: the model to add
        :return: the added model instance
        """
        async with db.scoped_session(self.session_maker) as session:
            session.add(model)
            await session.flush()

            # Query within same session
            found = await self.select_by_id(session, model.id)  # pyright: ignore [reportAttributeAccessIssue]
            if found is None:  # pragma: no cover
                logger.error(
                    "Failed to retrieve model after add",
                    model_type=self.Model.__name__,
                    model_id=model.id,  # pyright: ignore
                )
                raise ValueError(
                    f"Can't find {self.Model.__name__} with ID {model.id} after session.add"  # pyright: ignore
                )
            return found

    async def add_all(self, models: List[T]) -> Sequence[T]:
        """
        Add a list of models to the repository. This will also add related objects
        :param models: the models to add
        :return: the added models instances
        """
        async with db.scoped_session(self.session_maker) as session:
            session.add_all(models)
            await session.flush()

            # Query within same session
            return await self.select_by_ids(session, [m.id for m in models])  # pyright: ignore [reportAttributeAccessIssue]

    def select(self, *entities: Any) -> Select:
        """Create a new SELECT statement.

        Returns:
            A SQLAlchemy Select object configured with the provided entities
            or this repository's model if no entities provided.
        """
        if not entities:
            entities = (self.Model,)
        return select(*entities)

    async def find_all(self, skip: int = 0, limit: Optional[int] = None) -> Sequence[T]:
        """Fetch records from the database with pagination."""
        logger.debug(f"Finding all {self.Model.__name__} (skip={skip}, limit={limit})")

        async with db.scoped_session(self.session_maker) as session:
            query = select(self.Model).offset(skip).options(*self.get_load_options())
            if limit:
                query = query.limit(limit)

            result = await session.execute(query)

            items = result.scalars().all()
            logger.debug(f"Found {len(items)} {self.Model.__name__} records")
            return items

    async def find_by_id(self, entity_id: int) -> Optional[T]:
        """Fetch an entity by its unique identifier."""
        logger.debug(f"Finding {self.Model.__name__} by ID: {entity_id}")

        async with db.scoped_session(self.session_maker) as session:
            return await self.select_by_id(session, entity_id)

    async def find_by_ids(self, ids: List[int]) -> Sequence[T]:
        """Fetch multiple entities by their identifiers in a single query."""
        logger.debug(f"Finding {self.Model.__name__} by IDs: {ids}")

        async with db.scoped_session(self.session_maker) as session:
            return await self.select_by_ids(session, ids)

    async def find_one(self, query: Select[tuple[T]]) -> Optional[T]:
        """Execute a query and retrieve a single record."""
        logger.debug(f"Finding one {self.Model.__name__} with query: {query}")

        # add in load options
        query = query.options(*self.get_load_options())
        result = await self.execute_query(query)
        entity = result.scalars().one_or_none()

        if entity:
            logger.debug(f"Found {self.Model.__name__}: {getattr(entity, 'id', None)}")
        else:
            logger.debug(f"No {self.Model.__name__} found")
        return entity

    async def create(self, data: dict) -> T:
        """Create a new record from a model instance."""
        logger.debug(f"Creating {self.Model.__name__} from entity_data: {data}")
        async with db.scoped_session(self.session_maker) as session:
            # Only include valid columns that are provided in entity_data
            model_data = self.get_model_data(data)
            model = self.Model(**model_data)
            session.add(model)
            await session.flush()

            return_instance = await self.select_by_id(session, model.id)  # pyright: ignore [reportAttributeAccessIssue]
            if return_instance is None:  # pragma: no cover
                logger.error(
                    "Failed to retrieve model after create",
                    model_type=self.Model.__name__,
                    model_id=model.id,  # pyright: ignore
                )
                raise ValueError(
                    f"Can't find {self.Model.__name__} with ID {model.id} after session.add"  # pyright: ignore
                )
            return return_instance

    async def create_all(self, data_list: List[dict]) -> Sequence[T]:
        """Create multiple records in a single transaction."""
        logger.debug(f"Bulk creating {len(data_list)} {self.Model.__name__} instances")

        async with db.scoped_session(self.session_maker) as session:
            # Only include valid columns that are provided in entity_data
            model_list = [
                self.Model(
                    **self.get_model_data(d),
                )
                for d in data_list
            ]
            session.add_all(model_list)
            await session.flush()

            return await self.select_by_ids(session, [model.id for model in model_list])  # pyright: ignore [reportAttributeAccessIssue]

    async def update(self, entity_id: int, entity_data: dict | T) -> Optional[T]:
        """Update an entity with the given data."""
        logger.debug(f"Updating {self.Model.__name__} {entity_id} with data: {entity_data}")
        async with db.scoped_session(self.session_maker) as session:
            try:
                result = await session.execute(
                    select(self.Model).filter(self.primary_key == entity_id)
                )
                entity = result.scalars().one()

                if isinstance(entity_data, dict):
                    for key, value in entity_data.items():
                        if key in self.valid_columns:
                            setattr(entity, key, value)

                elif isinstance(entity_data, self.Model):
                    for column in self.Model.__table__.columns.keys():
                        setattr(entity, column, getattr(entity_data, column))

                await session.flush()  # Make sure changes are flushed
                await session.refresh(entity)  # Refresh

                logger.debug(f"Updated {self.Model.__name__}: {entity_id}")
                return await self.select_by_id(session, entity.id)  # pyright: ignore [reportAttributeAccessIssue]

            except NoResultFound:
                logger.debug(f"No {self.Model.__name__} found to update: {entity_id}")
                return None

    async def delete(self, entity_id: int) -> bool:
        """Delete an entity from the database."""
        logger.debug(f"Deleting {self.Model.__name__}: {entity_id}")
        async with db.scoped_session(self.session_maker) as session:
            try:
                result = await session.execute(
                    select(self.Model).filter(self.primary_key == entity_id)
                )
                entity = result.scalars().one()
                await session.delete(entity)

                logger.debug(f"Deleted {self.Model.__name__}: {entity_id}")
                return True
            except NoResultFound:
                logger.debug(f"No {self.Model.__name__} found to delete: {entity_id}")
                return False

    async def delete_by_ids(self, ids: List[int]) -> int:
        """Delete records matching given IDs."""
        logger.debug(f"Deleting {self.Model.__name__} by ids: {ids}")
        async with db.scoped_session(self.session_maker) as session:
            query = delete(self.Model).where(self.primary_key.in_(ids))
            result = await session.execute(query)
            logger.debug(f"Deleted {result.rowcount} records")
            return result.rowcount

    async def delete_by_fields(self, **filters: Any) -> bool:
        """Delete records matching given field values."""
        logger.debug(f"Deleting {self.Model.__name__} by fields: {filters}")
        async with db.scoped_session(self.session_maker) as session:
            conditions = [getattr(self.Model, field) == value for field, value in filters.items()]
            query = delete(self.Model).where(and_(*conditions))
            result = await session.execute(query)
            deleted = result.rowcount > 0
            logger.debug(f"Deleted {result.rowcount} records")
            return deleted

    async def count(self, query: Executable | None = None) -> int:
        """Count entities in the database table."""
        async with db.scoped_session(self.session_maker) as session:
            if query is None:
                query = select(func.count()).select_from(self.Model)
            result = await session.execute(query)
            scalar = result.scalar()
            count = scalar if scalar is not None else 0
            logger.debug(f"Counted {count} {self.Model.__name__} records")
            return count

    async def execute_query(self, query: Executable, use_query_options: bool = True) -> Result[Any]:
        """Execute a query asynchronously."""

        query = query.options(*self.get_load_options()) if use_query_options else query

        logger.debug(f"Executing query: {query}")
        async with db.scoped_session(self.session_maker) as session:
            result = await session.execute(query)
            logger.debug("Query executed successfully")
            return result

    def get_load_options(self) -> List[LoaderOption]:
        """Get list of loader options for eager loading relationships.
        Override in subclasses to specify what to load."""
        return []
