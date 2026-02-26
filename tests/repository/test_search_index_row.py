"""Tests for SearchIndexRow data structure."""

from datetime import datetime

from basic_memory.repository.search_index_row import SearchIndexRow


def test_content_display_limit_is_2000():
    """CONTENT_DISPLAY_LIMIT raised to 2000 for richer search result context."""
    assert SearchIndexRow.CONTENT_DISPLAY_LIMIT == 2000


def test_content_truncates_at_display_limit():
    """Content property truncates content_snippet at CONTENT_DISPLAY_LIMIT."""
    long_text = "a" * 3000
    row = SearchIndexRow(
        project_id=1,
        id=1,
        type="entity",
        file_path="test.md",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        content_snippet=long_text,
    )
    assert len(row.content) == 2000
    assert row.content == long_text[:2000]


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
