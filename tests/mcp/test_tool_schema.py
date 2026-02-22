"""Tests for schema MCP tools (validate, infer, diff).

Covers the tool function logic including success paths and error/exception paths.
The success-path tests use the full ASGI stack via the app fixture.
Error-path tests monkeypatch SchemaClient methods to trigger the except branch.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from basic_memory.mcp.tools.schema import schema_validate, schema_infer, schema_diff
from basic_memory.mcp.tools.write_note import write_note
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

    result = await schema_validate(
        note_type="person",
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

    result = await schema_validate(
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

    result = await schema_infer(
        note_type="person",
        project=test_project.name,
    )

    assert isinstance(result, InferenceReport)
    assert result.note_type == "person"
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

    result = await schema_diff(
        note_type="person",
        project=test_project.name,
    )

    assert isinstance(result, DriftReport)
    assert result.note_type == "person"


# --- write_note metadata → schema workflow ---


@pytest.mark.asyncio
async def test_write_note_metadata_creates_schema_note(app, test_project, sync_service):
    """Create a schema note via write_note(metadata=...), then validate against it.

    Proves the end-to-end workflow: write_note → sync → schema_validate.
    """
    project_path = Path(test_project.path)

    # 1. Create person notes via direct file write (content under test is the schema)
    for name in ["Alice", "Bob"]:
        _write_schema_file(
            project_path,
            f"people/{name}.md",
            PERSON_NOTE.format(name=name, permalink=name.lower()),
        )

    # 2. Create the schema note via write_note with metadata
    await write_note(
        title="Person",
        directory="schemas",
        note_type="schema",
        content="# Person\n\nSchema for person entities.",
        metadata={
            "entity": "person",
            "version": 1,
            "schema": {"name": "string", "role?": "string"},
            "settings": {"validation": "warn"},
        },
        project=test_project.name,
    )

    # 3. Sync picks up person notes written directly to disk
    await sync_service.sync(project_path)

    # 4. Validate — schema_validate should find the schema and validate person notes
    result = await schema_validate(
        note_type="person",
        project=test_project.name,
    )

    assert isinstance(result, ValidationReport)
    assert result.total_notes >= 2


@pytest.mark.asyncio
async def test_schema_title_mismatch_finds_by_metadata(app, test_project, sync_service):
    """Schema lookup works even when the schema title doesn't match the entity type.

    Regression test: the old text-search approach failed when the schema note's title
    (e.g. "Employee Schema") didn't textually match the entity type ("employee").
    The metadata-based lookup matches on entity_metadata['entity'] instead.
    """
    project_path = Path(test_project.path)

    # Schema title "Employee Schema" != entity type "employee"
    _write_schema_file(
        project_path,
        "schemas/EmployeeSchema.md",
        """\
---
title: Employee Schema
type: schema
entity: employee
version: 1
schema:
  name: string, full name
  department?: string, department name
settings:
  validation: warn
---

# Employee Schema

Schema for employee entities.
""",
    )

    # Create employee notes
    for name, dept in [("Alice", "Engineering"), ("Bob", "Marketing")]:
        _write_schema_file(
            project_path,
            f"employees/{name}.md",
            f"""\
---
title: {name}
type: employee
permalink: employees/{name.lower()}
---

# {name}

## Observations
- [name] {name}
- [department] {dept}
""",
        )

    await sync_service.sync(project_path)

    # Validate — must find "Employee Schema" via entity_metadata['entity'] == "employee"
    result = await schema_validate(
        note_type="employee",
        project=test_project.name,
    )

    assert isinstance(result, ValidationReport)
    assert result.total_notes == 2
    # Both notes have name + department, schema requires name and optionally department
    assert result.valid_count == 2


# --- Empty schema guard ---


@pytest.mark.asyncio
async def test_schema_infer_empty_schema_returns_guidance(app, test_project, sync_service):
    """When notes exist but no fields meet the threshold, return guidance instead of data."""
    project_path = Path(test_project.path)

    # Create notes with completely different observation categories so no field
    # reaches the 25% threshold across all notes
    for i, (name, category) in enumerate(
        [
            ("alpha", "color"),
            ("bravo", "shape"),
            ("charlie", "size"),
            ("delta", "weight"),
            ("echo", "temp"),
        ]
    ):
        _write_schema_file(
            project_path,
            f"things/{name}.md",
            f"""\
---
title: {name}
type: widget
permalink: things/{name}
---

# {name}

## Observations
- [{category}] some value
""",
        )

    await sync_service.sync(project_path)

    result = await schema_infer(
        note_type="widget",
        project=test_project.name,
    )

    # Should return guidance string, not an InferenceReport
    assert isinstance(result, str)
    assert "No Schema Pattern Found" in result
    assert "widget" in result
    assert "Suggestions" in result


# --- No schema found guards ---


@pytest.mark.asyncio
async def test_schema_validate_no_notes_returns_guidance(app, test_project, sync_service):
    """When no notes of the requested type exist, return guidance on creating notes."""
    result = await schema_validate(
        note_type="employee",
        project=test_project.name,
    )

    # Should return guidance about creating notes, not about missing schema
    assert isinstance(result, str)
    assert "No Notes Found" in result
    assert "employee" in result
    assert "write_note" in result
    assert "search_notes" in result


@pytest.mark.asyncio
async def test_schema_validate_no_schema_returns_guidance(app, test_project, sync_service):
    """When notes exist but no schema is defined, return guidance on creating one."""
    project_path = Path(test_project.path)

    # Create person notes but no schema note
    for name in ["Alice", "Bob"]:
        _write_schema_file(
            project_path,
            f"people/{name}.md",
            PERSON_NOTE.format(name=name, permalink=name.lower()),
        )

    await sync_service.sync(project_path)

    result = await schema_validate(
        note_type="person",
        project=test_project.name,
    )

    # Should return guidance string, not a ValidationReport
    assert isinstance(result, str)
    assert "No Schema Found" in result
    assert "person" in result
    assert "schema_infer" in result
    assert "How to Create a Schema" in result


@pytest.mark.asyncio
async def test_schema_diff_no_schema_returns_guidance(app, test_project, sync_service):
    """When no schema exists for the type, return guidance on creating one."""
    project_path = Path(test_project.path)

    # Create person notes but no schema note
    _write_schema_file(
        project_path,
        "people/Alice.md",
        PERSON_NOTE.format(name="Alice", permalink="alice"),
    )

    await sync_service.sync(project_path)

    result = await schema_diff(
        note_type="person",
        project=test_project.name,
    )

    # Should return guidance string, not a DriftReport
    assert isinstance(result, str)
    assert "No Schema Found" in result
    assert "person" in result
    assert "schema_infer" in result
    assert "How to Create a Schema" in result


# --- Error-path tests (monkeypatched SchemaClient) ---


@pytest.mark.asyncio
async def test_schema_validate_error_returns_guidance(app, test_project):
    """When SchemaClient.validate raises, the tool returns a troubleshooting string."""
    mock_validate = AsyncMock(side_effect=RuntimeError("connection lost"))

    with patch("basic_memory.mcp.clients.schema.SchemaClient.validate", mock_validate):
        result = await schema_validate(
            note_type="person",
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
        result = await schema_infer(
            note_type="person",
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
        result = await schema_diff(
            note_type="person",
            project=test_project.name,
        )

    assert isinstance(result, str)
    assert "Schema Diff Failed" in result
    assert "Troubleshooting" in result
