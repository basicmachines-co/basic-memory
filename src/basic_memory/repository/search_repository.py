"""Repository for search operations.

This module provides the search repository interface.
The actual repository implementations are backend-specific:
- SQLiteSearchRepository: Uses FTS5 virtual tables
- PostgresSearchRepository: Uses tsvector/tsquery with GIN indexes
"""

from datetime import datetime
from typing import Any, Callable, List, Optional, Protocol

from sqlalchemy import Result
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository.embedding_provider_factory import create_embedding_provider
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_repository_base import VectorSyncBatchResult
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
        categories: Optional[List[str]] = None,
        metadata_filters: Optional[dict] = None,
        retrieval_mode: SearchRetrievalMode = SearchRetrievalMode.FTS,
        min_similarity: Optional[float] = None,
        limit: int = 10,
        offset: int = 0,
        allow_relaxed: bool = False,
        session: AsyncSession | None = None,
    ) -> List[SearchIndexRow]:
        """Search across indexed content."""
        ...

    async def count(
        self,
        search_text: Optional[str] = None,
        permalink: Optional[str] = None,
        permalink_match: Optional[str] = None,
        title: Optional[str] = None,
        note_types: Optional[List[str]] = None,
        after_date: Optional[datetime] = None,
        search_item_types: Optional[List[SearchItemType]] = None,
        categories: Optional[List[str]] = None,
        metadata_filters: Optional[dict] = None,
        retrieval_mode: SearchRetrievalMode = SearchRetrievalMode.FTS,
        min_similarity: Optional[float] = None,
        allow_relaxed: bool = False,
    ) -> int:
        """Count indexed content matching the same filters as search."""
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

    async def delete_entity_vector_rows(self, entity_id: int) -> None:
        """Delete semantic vector chunks and embeddings for one entity."""
        ...

    async def sync_entity_vectors_batch(
        self,
        entity_ids: list[int],
        progress_callback: Optional[Callable[[int, int, int], Any]] = None,
    ) -> VectorSyncBatchResult:
        """Sync semantic vector chunks for a batch of entities."""
        ...

    async def execute_query(self, query, params: dict) -> Result:
        """Execute a raw SQL query."""
        ...


def create_search_repository(
    session_maker: async_sessionmaker[AsyncSession],
    project_id: int,
    app_config: BasicMemoryConfig,
    database_backend: Optional[DatabaseBackend] = None,
) -> SearchRepository:
    """Factory function to create the appropriate search repository based on database backend.

    Args:
        session_maker: SQLAlchemy async session maker
        project_id: Project ID for the repository
        app_config: Application config from the caller's composition root; backend
            detection and the shared embedding provider both derive from it
        database_backend: Optional explicit backend override

    Returns:
        SearchRepository: Backend-appropriate search repository instance
    """
    config = app_config
    if database_backend is None:
        database_backend = config.database_backend

    # Trigger: every request, sync batch, and project builds its own search repo.
    # Why: each repo __init__ would otherwise call create_embedding_provider(), and
    # the process-wide cache can be bypassed if its key ever drifts (#872), reloading
    # the ~2.3GB ONNX model and leaking memory in onnxruntime's CPU arena.
    # Outcome: resolve the cached singleton here once and inject it, so the provider
    # is the single source of truth across all callers of this factory.
    embedding_provider = None
    if config.semantic_search_enabled:
        embedding_provider = create_embedding_provider(config)

    if database_backend == DatabaseBackend.POSTGRES:  # pragma: no cover
        return PostgresSearchRepository(  # pragma: no cover
            session_maker,
            project_id=project_id,
            app_config=app_config,
            embedding_provider=embedding_provider,
        )
    else:
        return SQLiteSearchRepository(
            session_maker,
            project_id=project_id,
            app_config=app_config,
            embedding_provider=embedding_provider,
        )


__all__ = [
    "SearchRepository",
    "SearchIndexRow",
    "create_search_repository",
]
