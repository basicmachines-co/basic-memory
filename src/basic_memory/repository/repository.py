"""Base repository implementation."""

from typing import Type, Optional, Any, Sequence, TypeVar, List, Dict

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

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        Model: Type[T],
        project_id: Optional[int] = None,
    ):
        self.session_maker = session_maker
        self.project_id = project_id
        if Model:
            self.Model = Model
            self.mapper = inspect(self.Model).mapper
            self.primary_key: Column[Any] = self.mapper.primary_key[0]
            self.valid_columns = [column.key for column in self.mapper.columns]
            # Check if this model has a project_id column
            self.has_project_id = "project_id" in self.valid_columns

    def _set_project_id_if_needed(self, model: T) -> None:
        """Set project_id on model if needed and available."""
        if (
            self.has_project_id
            and self.project_id is not None
            and getattr(model, "project_id", None) is None
        ):
            setattr(model, "project_id", self.project_id)

    def get_model_data(self, entity_data):
        model_data = {
            k: v for k, v in entity_data.items() if k in self.valid_columns and v is not None
        }
        return model_data

    def _add_project_filter(self, query: Select) -> Select:
        """Add project_id filter to query if applicable.

        Args:
            query: The SQLAlchemy query to modify

        Returns:
            Updated query with project filter if applicable
        """
        if self.has_project_id and self.project_id is not None:
            query = query.filter(getattr(self.Model, "project_id") == self.project_id)
        return query

    async def select_by_id(self, session: AsyncSession, entity_id: int) -> Optional[T]:
        """Select an entity by ID using an existing session."""
        query = (
            select(self.Model)
            .filter(self.primary_key == entity_id)
            .options(*self.get_load_options())
        )
        # Add project filter if applicable
        query = self._add_project_filter(query)

        result = await session.execute(query)
        return result.scalars().one_or_none()

    async def select_by_ids(self, session: AsyncSession, ids: List[int]) -> Sequence[T]:
        """Select multiple entities by IDs using an existing session."""
        query = (
            select(self.Model).where(self.primary_key.in_(ids)).options(*self.get_load_options())
        )
        # Add project filter if applicable
        query = self._add_project_filter(query)

        result = await session.execute(query)
        return result.scalars().all()

    async def add(self, model: T) -> T:
        """
        Add a model to the repository. This will also add related objects
        :param model: the model to add
        :return: the added model instance
        """
        async with db.scoped_session(self.session_maker) as session:
            # Set project_id if applicable and not already set
            self._set_project_id_if_needed(model)

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
            # set the project id if not present in models
            for model in models:
                self._set_project_id_if_needed(model)

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
        query = select(*entities)

        # Add project filter if applicable
        return self._add_project_filter(query)

    async def find_all(self, skip: int = 0, limit: Optional[int] = None) -> Sequence[T]:
        """Fetch records from the database with pagination."""
        logger.debug(f"Finding all {self.Model.__name__} (skip={skip}, limit={limit})")

        async with db.scoped_session(self.session_maker) as session:
            query = select(self.Model).offset(skip).options(*self.get_load_options())
            # Add project filter if applicable
            query = self._add_project_filter(query)

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
        # add in load options
        query = query.options(*self.get_load_options())
        result = await self.execute_query(query)
        entity = result.scalars().one_or_none()

        if entity:
            logger.trace(f"Found {self.Model.__name__}: {getattr(entity, 'id', None)}")
        else:
            logger.trace(f"No {self.Model.__name__} found")
        return entity

    async def create(self, data: dict) -> T:
        """Create a new record from a model instance."""
        logger.debug(f"Creating {self.Model.__name__} from entity_data: {data}")
        async with db.scoped_session(self.session_maker) as session:
            # Only include valid columns that are provided in entity_data
            model_data = self.get_model_data(data)

            # Add project_id if applicable and not already provided
            if (
                self.has_project_id
                and self.project_id is not None
                and "project_id" not in model_data
            ):
                model_data["project_id"] = self.project_id

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
            model_list = []
            for d in data_list:
                model_data = self.get_model_data(d)

                # Add project_id if applicable and not already provided
                if (
                    self.has_project_id
                    and self.project_id is not None
                    and "project_id" not in model_data
                ):
                    model_data["project_id"] = self.project_id  # pragma: no cover

                model_list.append(self.Model(**model_data))

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
            conditions = [self.primary_key.in_(ids)]

            # Add project_id filter if applicable
            if self.has_project_id and self.project_id is not None:  # pragma: no cover
                conditions.append(getattr(self.Model, "project_id") == self.project_id)

            query = delete(self.Model).where(and_(*conditions))
            result = await session.execute(query)
            logger.debug(f"Deleted {result.rowcount} records")
            return result.rowcount

    async def delete_by_fields(self, **filters: Any) -> bool:
        """Delete records matching given field values."""
        logger.debug(f"Deleting {self.Model.__name__} by fields: {filters}")
        async with db.scoped_session(self.session_maker) as session:
            conditions = [getattr(self.Model, field) == value for field, value in filters.items()]

            # Add project_id filter if applicable
            if self.has_project_id and self.project_id is not None:
                conditions.append(getattr(self.Model, "project_id") == self.project_id)

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
                # Add project filter if applicable
                if (
                    isinstance(query, Select)
                    and self.has_project_id
                    and self.project_id is not None
                ):
                    query = query.where(
                        getattr(self.Model, "project_id") == self.project_id
                    )  # pragma: no cover

            result = await session.execute(query)
            scalar = result.scalar()
            count = scalar if scalar is not None else 0
            logger.debug(f"Counted {count} {self.Model.__name__} records")
            return count

    async def execute_query(
        self,
        query: Executable,
        params: Optional[Dict[str, Any]] = None,
        use_query_options: bool = True,
    ) -> Result[Any]:
        """Execute a query asynchronously."""

        query = query.options(*self.get_load_options()) if use_query_options else query
        logger.trace(f"Executing query: {query}, params: {params}")
        async with db.scoped_session(self.session_maker) as session:
            result = await session.execute(query, params)
            return result

    def get_load_options(self) -> List[LoaderOption]:
        """Get list of loader options for eager loading relationships.
        Override in subclasses to specify what to load."""
        return []
