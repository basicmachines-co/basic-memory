"""Repository for search operations.

This module provides the search repository interface.
The actual repository implementations are backend-specific:
- SQLiteSearchRepository: Uses FTS5 virtual tables
- PostgresSearchRepository: Uses tsvector/tsquery with GIN indexes
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Callable, List, Optional, Protocol

from sqlalchemy import Result
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository.embedding_provider_factory import create_embedding_provider
from basic_memory.repository.rerank_provider_factory import create_rerank_provider
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.search_index_row import SearchIndexRow
from basic_memory.repository.search_repository_base import ChunkManifestRow
from basic_memory.repository.search_trace import SearchTraceCollector
from basic_memory.repository.semantic_vector_index_factory import (
    create_semantic_vector_index,
    resolve_semantic_vector_index_name,
)
from basic_memory.runtime.vector_sync import VectorSyncBatchResult
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.schemas.search import SearchItemType, SearchRetrievalMode
from basic_memory.temporal import TemporalFilter


class SearchRepository(Protocol):
    """Protocol defining the search repository interface.

    Both SQLite and Postgres implementations must satisfy this protocol.
    """

    session_maker: async_sessionmaker[AsyncSession]

    @property
    def project_id(self) -> int: ...

    @property
    def configured_embedding_model(self) -> str: ...

    @property
    def configured_vector_index(self) -> str: ...

    @property
    def configured_min_similarity(self) -> float: ...

    @property
    def configured_reranker_model(self) -> str | None: ...

    @property
    def configured_reranker_candidates(self) -> int: ...

    async def init_search_index(self) -> None:
        """Initialize the search index schema."""
        ...

    async def get_entity_physical_chunk_keys(self, entity_id: int) -> set[str] | None:
        """Return chunk keys with a live physical vector row, or None when
        physical storage is not inspectable."""
        ...

    async def semantic_effectively_enabled(self) -> bool:
        """Return whether semantic retrieval can actually run right now."""
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
        metadata_filters: Optional[dict[str, Any]] = None,
        file_path_prefix: Optional[str] = None,
        temporal: Optional[TemporalFilter] = None,
        retrieval_mode: SearchRetrievalMode = SearchRetrievalMode.FTS,
        min_similarity: Optional[float] = None,
        limit: int = 10,
        offset: int = 0,
        allow_relaxed: bool = False,
        session: AsyncSession | None = None,
        *,
        trace: SearchTraceCollector | None = None,
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
        metadata_filters: Optional[dict[str, Any]] = None,
        file_path_prefix: Optional[str] = None,
        temporal: Optional[TemporalFilter] = None,
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

    async def get_entity_search_rows(self, entity_id: int) -> list[SearchIndexRow]:
        """Return every search projection owned by one entity."""
        ...

    async def get_entity_chunk_manifest(self, entity_id: int) -> list[ChunkManifestRow]:
        """Return the stored vector-chunk manifest for one entity."""
        ...

    async def delete_by_permalink(self, permalink: str, search_item_type: SearchItemType) -> None:
        """Delete the row one permalink owns for the given row kind."""
        ...

    async def delete_project_search_rows(self) -> None:
        """Delete every full-text search row owned by this project."""
        ...

    async def delete_by_entity_id(self, entity_id: int) -> None:
        """Delete items by entity ID."""
        ...

    async def purge_stale_search_rows(self) -> int:
        """Delete full-text rows whose backing database row no longer exists."""
        ...

    async def sync_entity_vectors(self, entity_id: int) -> None:
        """Sync semantic vector chunks for an entity."""
        ...

    async def delete_entity_vector_rows(self, entity_id: int) -> None:
        """Delete semantic vector chunks and embeddings for one entity."""
        ...

    async def delete_external_entity_vectors(
        self,
        session: AsyncSession,
        entity_ids: Sequence[int],
    ) -> None:
        """Delete DB-first entity vectors through an extension adapter."""
        ...

    async def delete_project_vector_rows(
        self,
        *,
        strict_adapter_cleanup: bool = True,
        session: AsyncSession | None = None,
    ) -> None:
        """Delete all semantic vector chunks and embeddings for this project."""
        ...

    async def delete_stale_vector_rows(self) -> None:
        """Delete semantic vectors whose source entities no longer exist."""
        ...

    async def reconcile_vector_index(self) -> None:
        """Remove adapter vectors that have no current ready manifest row."""
        ...

    async def sync_entity_vectors_batch(
        self,
        entity_ids: list[int],
        progress_callback: Optional[Callable[[int, int, int], Any]] = None,
    ) -> VectorSyncBatchResult:
        """Sync semantic vector chunks for a batch of entities."""
        ...

    async def execute_query(self, query, params: dict[str, Any]) -> Result[Any]:
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
    vector_index_name = resolve_semantic_vector_index_name(config, database_backend)
    vector_index = None
    rerank_provider = None
    if config.semantic_search_enabled:
        embedding_provider = create_embedding_provider(config)
        vector_index_name, vector_index = create_semantic_vector_index(
            session_maker=session_maker,
            project_id=project_id,
            app_config=config,
            database_backend=database_backend,
            embedding_provider=embedding_provider,
        )
        # Returns None unless reranking is enabled; resolve the cached singleton
        # here so both backends share one process-wide reranker model.
        rerank_provider = create_rerank_provider(config)

    if database_backend == DatabaseBackend.POSTGRES:  # pragma: no cover
        return PostgresSearchRepository(  # pragma: no cover
            session_maker,
            project_id=project_id,
            app_config=app_config,
            embedding_provider=embedding_provider,
            vector_index_name=vector_index_name,
            vector_index=vector_index,
            rerank_provider=rerank_provider,
        )
    else:
        return SQLiteSearchRepository(
            session_maker,
            project_id=project_id,
            app_config=app_config,
            embedding_provider=embedding_provider,
            vector_index_name=vector_index_name,
            vector_index=vector_index,
            rerank_provider=rerank_provider,
        )


__all__ = [
    "SearchRepository",
    "SearchIndexRow",
    "create_search_repository",
]
