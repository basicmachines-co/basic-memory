"""Tests for schema MCP tools (validate, infer, diff).

Covers the tool function logic including success paths and error/exception paths.
The success-path tests use the full ASGI stack via the app fixture.
Error-path tests monkeypatch SchemaClient methods to trigger the except branch.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from basic_memory.mcp.tools.schema import schema_validate, schema_infer, schema_diff
from basic_memory.schemas.schema import ValidationReport, InferenceReport, DriftReport


# --- Helpers ---


def _write_schema_file(project_path: Path, filename: str, content: str):
    """Write a markdown file directly to disk (bypasses write_note frontmatter generation)."""
    path = project_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


PERSON_SCHEMA = """\
---
title: Person
type: schema
entity: person
version: 1
schema:
  name: string, full name
  role?: string, job title
settings:
  validation: warn
---

# Person

Schema for person entities.
"""


PERSON_NOTE = """\
---
title: {name}
type: person
permalink: people/{permalink}
---

# {name}

## Observations
- [name] {name}
- [role] Engineer
"""


# --- Success-path tests (full ASGI stack) ---


@pytest.mark.asyncio
async def test_schema_validate_by_type(app, test_project, sync_service):
    """Validate all notes of a given entity type."""
    project_path = Path(test_project.path)

    _write_schema_file(project_path, "schemas/Person.md", PERSON_SCHEMA)
    _write_schema_file(
        project_path,
        "people/Alice.md",
        PERSON_NOTE.format(name="Alice", permalink="alice"),
    )

    # Sync so the database picks up the files
    await sync_service.sync(project_path)

    result = await schema_validate.fn(
        entity_type="person",
        project=test_project.name,
    )

    assert isinstance(result, ValidationReport)
    assert result.total_notes >= 1


@pytest.mark.asyncio
async def test_schema_validate_by_identifier(app, test_project, sync_service):
    """Validate a specific note by identifier."""
    project_path = Path(test_project.path)

    _write_schema_file(project_path, "schemas/Person.md", PERSON_SCHEMA)
    _write_schema_file(
        project_path,
        "people/Alice.md",
        PERSON_NOTE.format(name="Alice", permalink="alice"),
    )

    await sync_service.sync(project_path)

    result = await schema_validate.fn(
        identifier="people/alice",
        project=test_project.name,
    )

    assert isinstance(result, ValidationReport)
    assert result.total_notes >= 1


@pytest.mark.asyncio
async def test_schema_infer(app, test_project, sync_service):
    """Infer a schema from existing notes."""
    project_path = Path(test_project.path)

    for name in ["Alice", "Bob", "Charlie"]:
        _write_schema_file(
            project_path,
            f"people/{name}.md",
            PERSON_NOTE.format(name=name, permalink=name.lower()),
        )

    await sync_service.sync(project_path)

    result = await schema_infer.fn(
        entity_type="person",
        project=test_project.name,
    )

    assert isinstance(result, InferenceReport)
    assert result.entity_type == "person"
    assert result.notes_analyzed >= 3


@pytest.mark.asyncio
async def test_schema_diff(app, test_project, sync_service):
    """Detect drift between schema and actual usage."""
    project_path = Path(test_project.path)

    _write_schema_file(project_path, "schemas/Person.md", PERSON_SCHEMA)

    # Create a person with an extra "hobby" field not in the schema
    _write_schema_file(
        project_path,
        "people/Dave.md",
        """\
---
title: Dave
type: person
permalink: people/dave
---

# Dave

## Observations
- [name] Dave
- [role] Manager
- [hobby] Chess
""",
    )

    await sync_service.sync(project_path)

    result = await schema_diff.fn(
        entity_type="person",
        project=test_project.name,
    )

    assert isinstance(result, DriftReport)
    assert result.entity_type == "person"


# --- Error-path tests (monkeypatched SchemaClient) ---


@pytest.mark.asyncio
async def test_schema_validate_error_returns_guidance(app, test_project):
    """When SchemaClient.validate raises, the tool returns a troubleshooting string."""
    mock_validate = AsyncMock(side_effect=RuntimeError("connection lost"))

    with patch("basic_memory.mcp.clients.schema.SchemaClient.validate", mock_validate):
        result = await schema_validate.fn(
            entity_type="person",
            project=test_project.name,
        )

    assert isinstance(result, str)
    assert "Schema Validation Failed" in result
    assert "Troubleshooting" in result


@pytest.mark.asyncio
async def test_schema_infer_error_returns_guidance(app, test_project):
    """When SchemaClient.infer raises, the tool returns a troubleshooting string."""
    mock_infer = AsyncMock(side_effect=RuntimeError("db unavailable"))

    with patch("basic_memory.mcp.clients.schema.SchemaClient.infer", mock_infer):
        result = await schema_infer.fn(
            entity_type="person",
            project=test_project.name,
        )

    assert isinstance(result, str)
    assert "Schema Inference Failed" in result
    assert "Troubleshooting" in result


@pytest.mark.asyncio
async def test_schema_diff_error_returns_guidance(app, test_project):
    """When SchemaClient.diff raises, the tool returns a troubleshooting string."""
    mock_diff = AsyncMock(side_effect=RuntimeError("network error"))

    with patch("basic_memory.mcp.clients.schema.SchemaClient.diff", mock_diff):
        result = await schema_diff.fn(
            entity_type="person",
            project=test_project.name,
        )

    assert isinstance(result, str)
    assert "Schema Diff Failed" in result
    assert "Troubleshooting" in result
