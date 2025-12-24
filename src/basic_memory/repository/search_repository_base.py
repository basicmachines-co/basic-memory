"""Abstract base class for search repository implementations."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional


from loguru import logger
from sqlalchemy import Executable, Result, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.schemas.search import SearchItemType
from basic_memory.repository.search_index_row import SearchIndexRow


class SearchRepositoryBase(ABC):
    """Abstract base class for backend-specific search repository implementations.

    This class defines the common interface that all search repositories must implement,
    regardless of whether they use SQLite FTS5 or Postgres tsvector for full-text search.

    Concrete implementations:
    - SQLiteSearchRepository: Uses FTS5 virtual tables with MATCH queries
    - PostgresSearchRepository: Uses tsvector/tsquery with GIN indexes
    """

    def __init__(self, session_maker: async_sessionmaker[AsyncSession], project_id: int):
        """Initialize with session maker and project_id filter.

        Args:
            session_maker: SQLAlchemy session maker
            project_id: Project ID to filter all operations by

        Raises:
            ValueError: If project_id is None or invalid
        """
        if project_id is None or project_id <= 0:  # pragma: no cover
            raise ValueError("A valid project_id is required for SearchRepository")

        self.session_maker = session_maker
        self.project_id = project_id

    @abstractmethod
    async def init_search_index(self) -> None:
        """Create or recreate the search index.

        Backend-specific implementations:
        - SQLite: CREATE VIRTUAL TABLE using FTS5
        - Postgres: CREATE TABLE with tsvector column and GIN indexes
        """
        pass

    @abstractmethod
    def _prepare_search_term(self, term: str, is_prefix: bool = True) -> str:
        """Prepare a search term for backend-specific query syntax.

        Args:
            term: The search term to prepare
            is_prefix: Whether to add prefix search capability

        Returns:
            Formatted search term for the backend

        Backend-specific implementations:
        - SQLite: Quotes FTS5 special characters, adds * wildcards
        - Postgres: Converts to tsquery syntax with :* prefix operator
        """
        pass

    @abstractmethod
    async def search(
        self,
        search_text: Optional[str] = None,
        permalink: Optional[str] = None,
        permalink_match: Optional[str] = None,
        title: Optional[str] = None,
        types: Optional[List[str]] = None,
        after_date: Optional[datetime] = None,
        search_item_types: Optional[List[SearchItemType]] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> List[SearchIndexRow]:
        """Search across all indexed content.

        Args:
            search_text: Full-text search across title and content
            permalink: Exact permalink match
            permalink_match: Permalink pattern match (supports *)
            title: Title search
            types: Filter by entity types (from metadata.entity_type)
            after_date: Filter by created_at > after_date
            search_item_types: Filter by SearchItemType (ENTITY, OBSERVATION, RELATION)
            limit: Maximum results to return
            offset: Number of results to skip

        Returns:
            List of SearchIndexRow results with relevance scores

        Backend-specific implementations:
        - SQLite: Uses MATCH operator and bm25() for scoring
        - Postgres: Uses @@ operator and ts_rank() for scoring
        """
        pass

    async def index_item(self, search_index_row: SearchIndexRow) -> None:
        """Index or update a single item.

        This implementation is shared across backends as it uses standard SQL INSERT.
        """

        async with db.scoped_session(self.session_maker) as session:
            # Delete existing record if any
            await session.execute(
                text(
                    "DELETE FROM search_index WHERE permalink = :permalink AND project_id = :project_id"
                ),
                {"permalink": search_index_row.permalink, "project_id": self.project_id},
            )

            # When using text() raw SQL, always serialize JSON to string
            # Both SQLite (TEXT) and Postgres (JSONB) accept JSON strings in raw SQL
            # The database driver/column type will handle conversion
            insert_data = search_index_row.to_insert(serialize_json=True)
            insert_data["project_id"] = self.project_id

            # Insert new record
            await session.execute(
                text("""
                    INSERT INTO search_index (
                        id, title, content_stems, content_snippet, permalink, file_path, type, metadata,
                        from_id, to_id, relation_type,
                        entity_id, category,
                        created_at, updated_at,
                        project_id
                    ) VALUES (
                        :id, :title, :content_stems, :content_snippet, :permalink, :file_path, :type, :metadata,
                        :from_id, :to_id, :relation_type,
                        :entity_id, :category,
                        :created_at, :updated_at,
                        :project_id
                    )
                """),
                insert_data,
            )
            logger.debug(f"indexed row {search_index_row}")
            await session.commit()

    async def bulk_index_items(self, search_index_rows: List[SearchIndexRow]) -> None:
        """Index multiple items in a single batch operation.

        This implementation is shared across backends as it uses standard SQL INSERT.

        Note: This method assumes that any existing records for the entity_id
        have already been deleted (typically via delete_by_entity_id).

        Args:
            search_index_rows: List of SearchIndexRow objects to index
        """

        if not search_index_rows:
            return

        async with db.scoped_session(self.session_maker) as session:
            # When using text() raw SQL, always serialize JSON to string
            # Both SQLite (TEXT) and Postgres (JSONB) accept JSON strings in raw SQL
            # The database driver/column type will handle conversion
            insert_data_list = []
            for row in search_index_rows:
                insert_data = row.to_insert(serialize_json=True)
                insert_data["project_id"] = self.project_id
                insert_data_list.append(insert_data)

            # Batch insert all records using executemany
            await session.execute(
                text("""
                    INSERT INTO search_index (
                        id, title, content_stems, content_snippet, permalink, file_path, type, metadata,
                        from_id, to_id, relation_type,
                        entity_id, category,
                        created_at, updated_at,
                        project_id
                    ) VALUES (
                        :id, :title, :content_stems, :content_snippet, :permalink, :file_path, :type, :metadata,
                        :from_id, :to_id, :relation_type,
                        :entity_id, :category,
                        :created_at, :updated_at,
                        :project_id
                    )
                """),
                insert_data_list,
            )
            logger.debug(f"Bulk indexed {len(search_index_rows)} rows")
            await session.commit()

    async def delete_by_entity_id(self, entity_id: int) -> None:
        """Delete all search index entries for an entity.

        This implementation is shared across backends as it uses standard SQL DELETE.
        """
        async with db.scoped_session(self.session_maker) as session:
            await session.execute(
                text(
                    "DELETE FROM search_index WHERE entity_id = :entity_id AND project_id = :project_id"
                ),
                {"entity_id": entity_id, "project_id": self.project_id},
            )
            await session.commit()

    async def delete_by_permalink(self, permalink: str) -> None:
        """Delete a search index entry by permalink.

        This implementation is shared across backends as it uses standard SQL DELETE.
        """
        async with db.scoped_session(self.session_maker) as session:
            await session.execute(
                text(
                    "DELETE FROM search_index WHERE permalink = :permalink AND project_id = :project_id"
                ),
                {"permalink": permalink, "project_id": self.project_id},
            )
            await session.commit()

    async def execute_query(
        self,
        query: Executable,
        params: Dict[str, Any],
    ) -> Result[Any]:
        """Execute a query asynchronously.

        This implementation is shared across backends for utility query execution.
        """
        import time

        async with db.scoped_session(self.session_maker) as session:
            start_time = time.perf_counter()
            result = await session.execute(query, params)
            end_time = time.perf_counter()
            elapsed_time = end_time - start_time
            logger.debug(f"Query executed successfully in {elapsed_time:.2f}s.")
            return result
