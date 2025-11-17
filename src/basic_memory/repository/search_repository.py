"""Repository for search operations.

This module provides the search repository interface.
The actual repository implementations are backend-specific:
- SQLiteSearchRepository: Uses FTS5 virtual tables
- PostgresSearchRepository: Uses tsvector/tsquery with GIN indexes

For backward compatibility, SearchRepository is aliased to SQLiteSearchRepository.
"""

# Re-export SearchIndexRow for backward compatibility
from basic_memory.repository.search_index_row import SearchIndexRow

# Re-export backend-specific implementations
from basic_memory.repository.search_repository_base import SearchRepositoryBase
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository

# For backward compatibility, alias SearchRepository to SQLiteSearchRepository
# This will be replaced by a factory function in deps.py
SearchRepository = SQLiteSearchRepository

__all__ = [
    "SearchIndexRow",
    "SearchRepository",
    "SearchRepositoryBase",
    "SQLiteSearchRepository",
]
