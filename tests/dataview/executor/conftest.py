"""Pytest fixtures for executor tests."""

import pytest


@pytest.fixture
def note_with_frontmatter():
    """Note with frontmatter fields."""
    return {
        "id": 1,
        "title": "Test Note",
        "path": "test.md",
        "folder": "test",
        "content": "# Test\n\n- [ ] Task 1\n- [x] Task 2",
        "created_at": "2026-01-01",
        "updated_at": "2026-01-10",
        "frontmatter": {
            "status": "active",
            "priority": 1,
            "tags": ["test", "dev"],
            "due": "2026-01-15",
        },
    }


@pytest.fixture
def note_without_frontmatter():
    """Note without frontmatter."""
    return {
        "id": 2,
        "title": "Simple Note",
        "path": "simple.md",
        "folder": "notes",
        "content": "Just content",
        "created_at": "2026-01-01",
        "updated_at": "2026-01-01",
    }
