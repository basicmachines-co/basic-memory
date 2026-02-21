"""Unit tests for backend-specific distance-to-similarity conversions."""

import pytest

from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository


def test_sqlite_distance_to_similarity_formula():
    """SQLite converts L2 distance to cosine similarity for normalized vectors."""
    repo = SQLiteSearchRepository.__new__(SQLiteSearchRepository)

    assert repo._distance_to_similarity(0.0) == 1.0
    assert repo._distance_to_similarity(1.0) == pytest.approx(0.5)
    assert repo._distance_to_similarity(2.0) == 0.0


def test_postgres_distance_to_similarity_formula():
    """Postgres converts pgvector cosine distance to cosine similarity."""
    repo = PostgresSearchRepository.__new__(PostgresSearchRepository)

    assert repo._distance_to_similarity(0.0) == 1.0
    assert repo._distance_to_similarity(1.0) == 0.0
    assert repo._distance_to_similarity(2.0) == 0.0
