"""Tests for DataviewExecutor with nested note structure.

This test file verifies that the executor correctly handles notes with nested
file structure (as provided by sync_service.py) in addition to flat structure.

Bug context:
- sync_service.py provides notes with structure: {"file": {"path": "...", "folder": "..."}, ...}
- executor.py:_filter_by_from() expected: {"path": "...", ...}
- Result: FROM clause never matched, queries returned 0 results
"""

import pytest

from basic_memory.dataview.executor.executor import DataviewExecutor
from basic_memory.dataview.parser import DataviewParser


@pytest.fixture
def notes_with_nested_structure():
    """Notes with nested file structure (as provided by sync_service)."""
    return [
        {
            "id": 1,
            "title": "Project Alpha",
            "file": {
                "path": "1. projects/Project Alpha.md",
                "folder": "1. projects",
            },
            "content": "# Project Alpha\n\n- [ ] Task 1",
            "created_at": "2026-01-01",
            "updated_at": "2026-01-10",
            "frontmatter": {
                "type": "project",
                "status": "active",
                "priority": 1,
                "tags": ["project", "dev"],
            },
        },
        {
            "id": 2,
            "title": "Project Beta",
            "file": {
                "path": "1. projects/Project Beta.md",
                "folder": "1. projects",
            },
            "content": "# Project Beta",
            "created_at": "2026-01-05",
            "updated_at": "2026-01-11",
            "frontmatter": {
                "type": "project",
                "status": "archived",
                "priority": 2,
                "tags": ["project"],
            },
        },
        {
            "id": 3,
            "title": "Area Dev",
            "file": {
                "path": "2. areas/Area Dev.md",
                "folder": "2. areas",
            },
            "content": "# Dev Area",
            "created_at": "2026-01-01",
            "updated_at": "2026-01-12",
            "frontmatter": {
                "type": "area",
                "status": "active",
                "tags": ["area", "dev"],
            },
        },
    ]


@pytest.fixture
def notes_with_flat_structure():
    """Notes with flat structure (legacy format)."""
    return [
        {
            "id": 1,
            "title": "Project Alpha",
            "path": "1. projects/Project Alpha.md",
            "folder": "1. projects",
            "content": "# Project Alpha\n\n- [ ] Task 1",
            "created_at": "2026-01-01",
            "updated_at": "2026-01-10",
            "frontmatter": {
                "type": "project",
                "status": "active",
                "priority": 1,
                "tags": ["project", "dev"],
            },
        },
        {
            "id": 2,
            "title": "Project Beta",
            "path": "1. projects/Project Beta.md",
            "folder": "1. projects",
            "content": "# Project Beta",
            "created_at": "2026-01-05",
            "updated_at": "2026-01-11",
            "frontmatter": {
                "type": "project",
                "status": "archived",
                "priority": 2,
                "tags": ["project"],
            },
        },
        {
            "id": 3,
            "title": "Area Dev",
            "path": "2. areas/Area Dev.md",
            "folder": "2. areas",
            "content": "# Dev Area",
            "created_at": "2026-01-01",
            "updated_at": "2026-01-12",
            "frontmatter": {
                "type": "area",
                "status": "active",
                "tags": ["area", "dev"],
            },
        },
    ]


class TestExecutorNestedStructure:
    """Test executor with nested file structure."""

    def test_from_clause_with_nested_structure(self, notes_with_nested_structure):
        """Test FROM clause works with nested file structure."""
        query = DataviewParser.parse('LIST FROM "1. projects"')
        executor = DataviewExecutor(notes_with_nested_structure)
        result = executor.execute(query)

        # Should match notes in "1. projects" folder
        assert "[[Project Alpha]]" in result
        assert "[[Project Beta]]" in result
        assert "[[Area Dev]]" not in result

    def test_from_clause_with_flat_structure(self, notes_with_flat_structure):
        """Test FROM clause works with flat structure (legacy)."""
        query = DataviewParser.parse('LIST FROM "1. projects"')
        executor = DataviewExecutor(notes_with_flat_structure)
        result = executor.execute(query)

        # Should match notes in "1. projects" folder
        assert "[[Project Alpha]]" in result
        assert "[[Project Beta]]" in result
        assert "[[Area Dev]]" not in result

    def test_from_clause_exact_folder_nested(self, notes_with_nested_structure):
        """Test FROM with exact folder match (nested structure)."""
        query = DataviewParser.parse('LIST FROM "2. areas"')
        executor = DataviewExecutor(notes_with_nested_structure)
        result = executor.execute(query)

        assert "Area Dev" in result
        assert "Project Alpha" not in result
        assert "Project Beta" not in result

    def test_from_clause_exact_folder_flat(self, notes_with_flat_structure):
        """Test FROM with exact folder match (flat structure)."""
        query = DataviewParser.parse('LIST FROM "2. areas"')
        executor = DataviewExecutor(notes_with_flat_structure)
        result = executor.execute(query)

        assert "Area Dev" in result
        assert "Project Alpha" not in result
        assert "Project Beta" not in result

    def test_table_query_with_nested_structure(self, notes_with_nested_structure):
        """Test TABLE query with nested structure."""
        query = DataviewParser.parse('TABLE title, status FROM "1. projects"')
        executor = DataviewExecutor(notes_with_nested_structure)
        result = executor.execute(query)

        assert "| Project Alpha | active |" in result
        assert "| Project Beta | archived |" in result
        assert "Area Dev" not in result

    def test_task_query_with_nested_structure(self, notes_with_nested_structure):
        """Test TASK query with nested structure."""
        query = DataviewParser.parse('TASK FROM "1. projects"')
        executor = DataviewExecutor(notes_with_nested_structure)
        result = executor.execute(query)

        # Should only include tasks from projects folder
        assert "Task" in result

    def test_mixed_structures(self):
        """Test executor handles mixed flat and nested structures."""
        mixed_notes = [
            {
                "id": 1,
                "title": "Flat Note",
                "path": "1. projects/Flat.md",
                "folder": "1. projects",
                "content": "# Flat",
                "frontmatter": {"status": "active"},
            },
            {
                "id": 2,
                "title": "Nested Note",
                "file": {
                    "path": "1. projects/Nested.md",
                    "folder": "1. projects",
                },
                "content": "# Nested",
                "frontmatter": {"status": "active"},
            },
        ]

        query = DataviewParser.parse('LIST FROM "1. projects"')
        executor = DataviewExecutor(mixed_notes)
        result = executor.execute(query)

        # Both should be matched
        assert "[[Flat Note]]" in result
        assert "[[Nested Note]]" in result
