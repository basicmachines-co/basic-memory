"""Pytest fixtures for Dataview tests."""

import pytest
from datetime import date


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


@pytest.fixture
def sample_notes():
    """Sample notes for testing."""
    return [
        {
            "id": 1,
            "title": "Project Alpha",
            "path": "1. projects/Project Alpha.md",
            "folder": "1. projects",
            "content": "# Project Alpha\n\n- [ ] Task 1\n- [x] Task 2\n- [ ] Task 3",
            "created_at": "2026-01-01",
            "updated_at": "2026-01-10",
            "frontmatter": {
                "type": "project",
                "status": "active",
                "due": "2026-01-15",
                "priority": 1,
                "tags": ["project", "dev"],
            },
        },
        {
            "id": 2,
            "title": "Project Beta",
            "path": "1. projects/Project Beta.md",
            "folder": "1. projects",
            "content": "# Project Beta\n\n- [x] Done task",
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
            "content": "# Dev Area\n\n- [ ] Ongoing task",
            "created_at": "2026-01-01",
            "updated_at": "2026-01-12",
            "frontmatter": {
                "type": "area",
                "status": "active",
                "tags": ["area", "dev"],
            },
        },
        {
            "id": 4,
            "title": "Resource Note",
            "path": "3. resources/Resource Note.md",
            "folder": "3. resources",
            "content": "# Resource\n\nSome content",
            "created_at": "2026-01-03",
            "updated_at": "2026-01-08",
            "frontmatter": {
                "type": "resource",
                "tags": ["reference"],
            },
        },
    ]


@pytest.fixture
def sample_queries():
    """Sample Dataview queries for testing."""
    return {
        "simple_list": 'LIST FROM "1. projects"',
        "list_with_where": 'LIST FROM "1. projects" WHERE status = "active"',
        "task_query": "TASK WHERE !completed",
        "table_query": "TABLE title, status FROM #project",
        "table_with_alias": 'TABLE title AS "Project Name", status FROM "1. projects"',
        "complex_query": 'TABLE title, status, due FROM #project WHERE status != "archived" SORT due ASC LIMIT 10',
        "sort_multiple": "TABLE title, priority FROM #project SORT priority ASC, title DESC",
        "function_query": 'TABLE title FROM "1. projects" WHERE contains(tags, "dev")',
    }


@pytest.fixture
def markdown_with_dataview():
    """Markdown content with Dataview queries."""
    return """# My Note

Some content here.

```dataview
LIST FROM "1. projects"
WHERE status = "active"
```

More content.

```dataview
TABLE title, status
FROM #project
SORT title ASC
```

Inline query: `= this.status`

Another inline: `= length(this.tags)`
"""


@pytest.fixture
def markdown_with_tasks():
    """Markdown content with tasks."""
    return """# Project Tasks

## Todo
- [ ] Task 1
- [ ] Task 2
  - [ ] Subtask 2.1
  - [x] Subtask 2.2
- [x] Task 3

## Done
- [x] Completed task
"""
