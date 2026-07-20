"""Tests for SearchIndexRow data structure."""

from datetime import datetime
from decimal import Decimal

from basic_memory.repository.search_index_row import SearchIndexRow


def test_from_mapping_normalizes_database_values():
    """Database rows share one hydration path across SQLite and Postgres."""
    now = datetime.now()
    row = SearchIndexRow.from_mapping(
        {
            "project_id": 1,
            "id": 2,
            "type": "observation",
            "file_path": "notes/example.md",
            "created_at": now,
            "updated_at": now,
            "metadata": '{"tags": ["example"]}',
            "score": Decimal("0.25"),
            "entity_id": 3,
            "content_snippet": "Shared hydration",
        }
    )

    assert row.metadata == {"tags": ["example"]}
    assert row.score == 0.25
    assert row.entity_id == 3
    assert row.content_snippet == "Shared hydration"


def test_content_display_limit_is_4000():
    """CONTENT_DISPLAY_LIMIT raised to 4000 for richer search result context."""
    assert SearchIndexRow.CONTENT_DISPLAY_LIMIT == 4000


def test_content_truncates_at_display_limit():
    """Content property truncates content_snippet at CONTENT_DISPLAY_LIMIT."""
    long_text = "a" * 5000
    row = SearchIndexRow(
        project_id=1,
        id=1,
        type="entity",
        file_path="test.md",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        content_snippet=long_text,
    )
    assert len(row.content) == 4000
    assert row.content == long_text[:4000]


def test_content_returns_full_snippet_when_under_limit():
    """Content property returns full content_snippet when under the limit."""
    short_text = "Short note content"
    row = SearchIndexRow(
        project_id=1,
        id=1,
        type="entity",
        file_path="test.md",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        content_snippet=short_text,
    )
    assert row.content == short_text
