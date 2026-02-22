"""Repository for search operations.

This module provides the search repository interface.
The actual repository implementations are backend-specific:
- SQLiteSearchRepository: Uses FTS5 virtual tables
- PostgresSearchRepository: Uses tsvector/tsquery with GIN indexes
"""

from datetime import datetime
from typing import List, Optional, Protocol

from sqlalchemy import Result
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.config import BasicMemoryConfig, ConfigManager, DatabaseBackend
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode


class SearchRepository(Protocol):
    """Protocol defining the search repository interface.

    Both SQLite and Postgres implementations must satisfy this protocol.
    """

    project_id: int

    async def init_search_index(self) -> None:
        """Initialize the search index schema."""
        ...

    async def search(
        self,
        search_text: Optional[str] = None,
        permalink: Optional[str] = None,
        permalink_match: Optional[str] = None,
        title: Optional[str] = None,
        note_types: Optional[List[str]] = None,
        after_date: Optional[datetime] = None,
        search_item_types: Optional[List[SearchItemType]] = None,
        metadata_filters: Optional[dict] = None,
        retrieval_mode: SearchRetrievalMode = SearchRetrievalMode.FTS,
        min_similarity: Optional[float] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> List[SearchIndexRow]:
        """Search across indexed content."""
        ...

    async def index_item(self, search_index_row: SearchIndexRow) -> None:
        """Index a single item."""
        ...

    async def bulk_index_items(self, search_index_rows: List[SearchIndexRow]) -> None:
        """Index multiple items in a batch."""
        ...

    async def delete_by_permalink(self, permalink: str) -> None:
        """Delete item by permalink."""
        ...

    async def delete_by_entity_id(self, entity_id: int) -> None:
        """Delete items by entity ID."""
        ...

    async def sync_entity_vectors(self, entity_id: int) -> None:
        """Sync semantic vector chunks for an entity."""
        ...

    async def execute_query(self, query, params: dict) -> Result:
        """Execute a raw SQL query."""
        ...


def create_search_repository(
    session_maker: async_sessionmaker[AsyncSession],
    project_id: int,
    app_config: Optional[BasicMemoryConfig] = None,
    database_backend: Optional[DatabaseBackend] = None,
) -> SearchRepository:
    """Factory function to create the appropriate search repository based on database backend.

    Args:
        session_maker: SQLAlchemy async session maker
        project_id: Project ID for the repository
        database_backend: Optional explicit backend. If not provided, reads from ConfigManager.
            Prefer passing explicitly from composition roots.

    Returns:
        SearchRepository: Backend-appropriate search repository instance
    """
    # Prefer explicit parameter; fall back to ConfigManager for backwards compatibility
    if database_backend is None:
        config = app_config or ConfigManager().config
        database_backend = config.database_backend

    if database_backend == DatabaseBackend.POSTGRES:  # pragma: no cover
        return PostgresSearchRepository(  # pragma: no cover
            session_maker,
            project_id=project_id,
            app_config=app_config,
        )
    else:
        return SQLiteSearchRepository(session_maker, project_id=project_id, app_config=app_config)


__all__ = [
    "SearchRepository",
    "SearchIndexRow",
    "create_search_repository",
]
