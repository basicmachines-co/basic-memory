"""Tests for bm_schema (validate, infer, diff) through the full ASGI stack."""

from collections.abc import Awaitable, Callable
from pathlib import Path

import json

import pytest
from fastmcp.exceptions import ToolError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.index.local_project import LocalProjectIndexRunner
from basic_memory.mcp.tools import bm_schema, bm_write
from basic_memory.models import Project
from basic_memory.repository import ProjectRepository


# --- Helpers ---


def _write_schema_file(project_path: Path, filename: str, content: str):
    """Write a markdown file directly to disk (bypasses bm_write frontmatter generation)."""
    path = project_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def index_project(
    project_repository: ProjectRepository,
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> Callable[[], Awaitable[None]]:
    """Return a project-index runner for files written directly by these tests."""

    async def run_index() -> None:
        runner = LocalProjectIndexRunner(
            project_repository=project_repository,
            session_maker=session_maker,
        )
        await runner.index_project(test_project.id, force_full=True)

    return run_index


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


MEETING_SCHEMA = """\
---
title: Meeting
type: schema
entity: meeting
version: 1
schema:
  date: string, meeting date
settings:
  validation: warn
---

# Meeting

Schema for meeting entities.
"""


# --- Success-path tests (full ASGI stack) ---


@pytest.mark.asyncio
async def test_schema_validate_by_type(app, test_project, index_project):
    """Validate all notes of a given entity type."""
    project_path = Path(test_project.path)

    _write_schema_file(project_path, "schemas/Person.md", PERSON_SCHEMA)
    _write_schema_file(
        project_path,
        "people/Alice.md",
        PERSON_NOTE.format(name="Alice", permalink="alice"),
    )

    # Index so the database picks up the files written directly to disk.
    await index_project()

    result = await bm_schema(
        command="validate",
        note_type="person",
        project=test_project.name,
    )

    assert isinstance(result, str)
    assert "Schema Validation: person" in result
    assert "Notes: 1" in result
    assert "**Alice**" in result
    assert "valid" in result


@pytest.mark.asyncio
async def test_schema_validate_json_output(app, test_project, index_project):
    """JSON output returns a dict with full structured data."""
    project_path = Path(test_project.path)

    _write_schema_file(project_path, "schemas/Person.md", PERSON_SCHEMA)
    _write_schema_file(
        project_path,
        "people/Alice.md",
        PERSON_NOTE.format(name="Alice", permalink="alice"),
    )

    await index_project()

    result = await bm_schema(
        command="validate",
        note_type="person",
        project=test_project.name,
        output_format="json",
    )

    assert isinstance(result, dict)
    assert result["total_notes"] == 1
    assert result["valid_count"] == 1
    assert len(result["results"]) == 1
    assert result["results"][0]["note_identifier"] == "Alice"
    assert result["results"][0]["passed"] is True


@pytest.mark.asyncio
async def test_schema_validate_picoschema_modifier_descriptions(app, test_project, index_project):
    """Modifier descriptions should not become literal field names."""
    project_path = Path(test_project.path)

    _write_schema_file(
        project_path,
        "schemas/PicoTest.md",
        """\
---
title: PicoTest
type: schema
entity: pico_test
schema:
  name: string
  status(enum, current state): [active, inactive]
  tags(array, list of tags): string
settings:
  validation: warn
---

# PicoTest
""",
    )
    _write_schema_file(
        project_path,
        "pico/PicoTest1.md",
        """\
---
title: PicoTest1
type: pico_test
permalink: pico/pico-test-1
---

# PicoTest1

## Observations
- [name] PicoTest1
- [status] active
- [tags] foo
- [tags] bar
""",
    )

    await index_project()

    result = await bm_schema(
        command="validate",
        note_type="pico_test",
        project=test_project.name,
        output_format="json",
    )

    assert isinstance(result, dict)
    note_result = result["results"][0]
    field_statuses = {fr["field_name"]: fr["status"] for fr in note_result["field_results"]}
    assert result["valid_count"] == 1
    assert note_result["warnings"] == []
    assert note_result["unmatched_observations"] == {}
    assert field_statuses == {
        "name": "present",
        "status": "present",
        "tags": "present",
    }


@pytest.mark.asyncio
async def test_schema_validate_by_identifier(app, test_project, index_project):
    """Validate a specific note by identifier."""
    project_path = Path(test_project.path)

    _write_schema_file(project_path, "schemas/Person.md", PERSON_SCHEMA)
    _write_schema_file(
        project_path,
        "people/Alice.md",
        PERSON_NOTE.format(name="Alice", permalink="alice"),
    )

    await index_project()

    result = await bm_schema(
        command="validate",
        identifier="people/alice",
        project=test_project.name,
    )

    assert isinstance(result, str)
    assert "**Alice**" in result
    assert "valid" in result


@pytest.mark.asyncio
async def test_schema_validate_by_title(app, test_project, index_project):
    """Validate a specific note by title (not permalink).

    Regression test for issue #33: validating with identifier="Note Title")
    returned 0 notes because the router only searched by permalink.
    """
    project_path = Path(test_project.path)

    _write_schema_file(project_path, "schemas/Person.md", PERSON_SCHEMA)
    _write_schema_file(
        project_path,
        "people/Alice.md",
        PERSON_NOTE.format(name="Alice", permalink="alice"),
    )

    await index_project()

    # Use the title "Alice" instead of the permalink "people/alice"
    result = await bm_schema(
        command="validate",
        identifier="Alice",
        project=test_project.name,
    )

    assert isinstance(result, str)
    assert "**Alice**" in result
    assert "Notes: 1" in result
    assert "valid" in result


@pytest.mark.asyncio
async def test_schema_validate_identifier_no_schema_refuses_with_guidance(
    app, test_project, index_project
):
    """When a note exists but no schema is defined, refuse with guidance.

    Regression test for issue #33: when validating a single note by identifier
    and no schema exists, the tool should refuse with guidance instead of an empty report.
    """
    project_path = Path(test_project.path)

    # Create a person note but no schema note
    _write_schema_file(
        project_path,
        "people/Alice.md",
        PERSON_NOTE.format(name="Alice", permalink="alice"),
    )

    await index_project()

    # Nothing to report is a refusal now, not a success (C11, #1813).
    with pytest.raises(ToolError) as refused:
        await bm_schema(
            command="validate",
            identifier="Alice",
            project=test_project.name,
        )
    result = str(refused.value)

    # The refusal carries guidance about the missing schema
    assert isinstance(result, str)
    assert "No Schema Found" in result
    assert "person" in result


@pytest.mark.asyncio
async def test_schema_infer(app, test_project, index_project):
    """Infer a schema from existing notes."""
    project_path = Path(test_project.path)

    for name in ["Alice", "Bob", "Charlie"]:
        _write_schema_file(
            project_path,
            f"people/{name}.md",
            PERSON_NOTE.format(name=name, permalink=name.lower()),
        )

    await index_project()

    result = await bm_schema(
        command="infer",
        note_type="person",
        project=test_project.name,
    )

    assert isinstance(result, str)
    assert "Schema Inference: person" in result
    assert "Notes analyzed: 3" in result
    assert "Field Frequencies" in result
    assert "**name**" in result
    assert "**role**" in result


@pytest.mark.asyncio
async def test_schema_diff(app, test_project, index_project):
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

    await index_project()

    result = await bm_schema(
        command="diff",
        note_type="person",
        project=test_project.name,
    )

    assert isinstance(result, str)
    assert "Schema Drift: person" in result
    # Dave has a "hobby" field not in the schema, so drift should be detected
    assert "**hobby**" in result


# --- All-types validation (#1013) ---


@pytest.mark.asyncio
async def test_schema_validate_all_types(app, test_project, index_project):
    """With no arguments, validate every note type that has a schema defined."""
    project_path = Path(test_project.path)

    _write_schema_file(project_path, "schemas/Person.md", PERSON_SCHEMA)
    # Meeting schema exists but no meeting notes do
    _write_schema_file(project_path, "schemas/Meeting.md", MEETING_SCHEMA)
    _write_schema_file(
        project_path,
        "people/Alice.md",
        PERSON_NOTE.format(name="Alice", permalink="alice"),
    )

    await index_project()

    result = await bm_schema(command="validate", project=test_project.name)

    assert isinstance(result, str)
    assert "Schema Validation: all" in result
    assert "## By Type" in result
    assert "- **person**: 1/1 valid" in result
    assert "- **meeting**: no notes" in result
    assert "**Alice**" in result


@pytest.mark.asyncio
async def test_schema_validate_all_types_json(app, test_project, index_project):
    """JSON output in all-types mode includes the per-type breakdown."""
    project_path = Path(test_project.path)

    _write_schema_file(project_path, "schemas/Person.md", PERSON_SCHEMA)
    _write_schema_file(project_path, "schemas/Meeting.md", MEETING_SCHEMA)
    _write_schema_file(
        project_path,
        "people/Alice.md",
        PERSON_NOTE.format(name="Alice", permalink="alice"),
    )

    await index_project()

    result = await bm_schema(command="validate", project=test_project.name, output_format="json")

    assert isinstance(result, dict)
    assert result["total_notes"] == 1
    assert result["valid_count"] == 1

    summaries = {s["note_type"]: s for s in result["type_summaries"]}
    assert set(summaries) == {"person", "meeting"}
    assert summaries["person"]["valid_count"] == 1
    assert summaries["meeting"]["total_entities"] == 0


@pytest.mark.asyncio
async def test_schema_validate_all_types_no_schemas_refuses_with_guidance(
    app, test_project, index_project
):
    """With no arguments and no schemas defined, refuse with guidance — not 'unknown'.

    Regression test for issue #1013: validate without note_type or
    identifier reported "No Notes Found of Type 'unknown'" instead of
    explaining that no schemas exist yet.
    """
    project_path = Path(test_project.path)

    # Notes exist, but no schema notes are defined
    _write_schema_file(
        project_path,
        "people/Alice.md",
        PERSON_NOTE.format(name="Alice", permalink="alice"),
    )

    await index_project()

    # Nothing to report is a refusal now, not a success (C11, #1813).
    with pytest.raises(ToolError) as refused:
        await bm_schema(command="validate", project=test_project.name)
    result = str(refused.value)

    assert isinstance(result, str)
    assert "No Schemas Defined" in result
    assert "unknown" not in result
    assert "bm_schema(command='infer'" in result


@pytest.mark.asyncio
async def test_schema_validate_all_types_no_schemas_json(app, test_project, index_project):
    """JSON output with no schemas defined returns a clear error dict."""
    # Nothing to report is a refusal now, not a success (C11, #1813).
    with pytest.raises(ToolError) as refused:
        await bm_schema(command="validate", project=test_project.name, output_format="json")
    result = json.loads(str(refused.value))

    assert result == {"error": "NOT_FOUND", "message": "No schemas defined in this project"}


# --- bm_write metadata → schema workflow ---


@pytest.mark.asyncio
async def test_bm_write_metadata_creates_schema_note(app, test_project, index_project):
    """Create a schema note via bm_write(metadata=...), then validate against it.

    Proves the end-to-end workflow: bm_write → index → bm_schema validate.
    """
    project_path = Path(test_project.path)

    # 1. Create person notes via direct file write (content under test is the schema)
    for name in ["Alice", "Bob"]:
        _write_schema_file(
            project_path,
            f"people/{name}.md",
            PERSON_NOTE.format(name=name, permalink=name.lower()),
        )

    # 2. Create the schema note via bm_write with metadata
    await bm_write(
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
    await index_project()

    # 4. Validate — bm_schema should find the schema and validate person notes
    result = await bm_schema(
        command="validate",
        note_type="person",
        project=test_project.name,
    )

    assert isinstance(result, str)
    assert "Schema Validation: person" in result
    assert "valid" in result


@pytest.mark.asyncio
async def test_schema_title_mismatch_finds_by_metadata(app, test_project, index_project):
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

    await index_project()

    # Validate — must find "Employee Schema" via entity_metadata['entity'] == "employee"
    result = await bm_schema(
        command="validate",
        note_type="employee",
        project=test_project.name,
    )

    assert isinstance(result, str)
    assert "Schema Validation: employee" in result
    assert "Notes: 2" in result
    assert "Valid: 2" in result
    # Both notes have name + department, schema requires name and optionally department
    assert "**Alice**" in result
    assert "**Bob**" in result


# --- Empty schema guard ---


@pytest.mark.asyncio
async def test_schema_infer_empty_schema_refuses_with_guidance(app, test_project, index_project):
    """When notes exist but no fields meet the threshold, refuse with guidance instead of data."""
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

    await index_project()

    # Nothing to report is a refusal now, not a success (C11, #1813).
    with pytest.raises(ToolError) as refused:
        await bm_schema(
            command="infer",
            note_type="widget",
            project=test_project.name,
        )
    result = str(refused.value)

    # The refusal carries guidance, not an InferenceReport
    assert isinstance(result, str)
    assert "No Schema Pattern Found" in result
    assert "widget" in result
    assert "Suggestions" in result


# --- No schema found guards ---


@pytest.mark.asyncio
async def test_schema_validate_no_notes_refuses_with_guidance(app, test_project, index_project):
    """When no notes of the requested type exist, refuse with guidance on creating notes."""
    # Nothing to report is a refusal now, not a success (C11, #1813).
    with pytest.raises(ToolError) as refused:
        await bm_schema(
            command="validate",
            note_type="employee",
            project=test_project.name,
        )
    result = str(refused.value)

    # The refusal is about creating notes, not about a missing schema
    assert isinstance(result, str)
    assert "No Notes Found" in result
    assert "employee" in result
    assert "bm_write" in result
    assert "bm_search" in result


@pytest.mark.asyncio
async def test_schema_validate_no_schema_refuses_with_guidance(app, test_project, index_project):
    """When notes exist but no schema is defined, refuse with guidance on creating one."""
    project_path = Path(test_project.path)

    # Create person notes but no schema note
    for name in ["Alice", "Bob"]:
        _write_schema_file(
            project_path,
            f"people/{name}.md",
            PERSON_NOTE.format(name=name, permalink=name.lower()),
        )

    await index_project()

    # Nothing to report is a refusal now, not a success (C11, #1813).
    with pytest.raises(ToolError) as refused:
        await bm_schema(
            command="validate",
            note_type="person",
            project=test_project.name,
        )
    result = str(refused.value)

    # The refusal carries guidance, not a ValidationReport
    assert isinstance(result, str)
    assert "No Schema Found" in result
    assert "person" in result
    assert "bm_schema(command='infer'" in result
    assert "How to Create a Schema" in result
    assert "bm_write" in result
    assert "bm reindex" not in result
    assert "file watcher" not in result


@pytest.mark.asyncio
async def test_schema_diff_no_schema_refuses_with_guidance(app, test_project, index_project):
    """When no schema exists for the type, refuse with guidance on creating one."""
    project_path = Path(test_project.path)

    # Create person notes but no schema note
    _write_schema_file(
        project_path,
        "people/Alice.md",
        PERSON_NOTE.format(name="Alice", permalink="alice"),
    )

    await index_project()

    # Nothing to report is a refusal now, not a success (C11, #1813).
    with pytest.raises(ToolError) as refused:
        await bm_schema(
            command="diff",
            note_type="person",
            project=test_project.name,
        )
    result = str(refused.value)

    # The refusal carries guidance, not a DriftReport
    assert isinstance(result, str)
    assert "No Schema Found" in result
    assert "person" in result
    assert "bm_schema(command='infer'" in result
    assert "How to Create a Schema" in result
    assert "bm_write" in result
    assert "bm reindex" not in result
    assert "file watcher" not in result


@pytest.mark.asyncio
async def test_schema_note_from_bm_write_found_without_reindex(app, test_project, index_project):
    """A schema note saved with bm_write is found at once; no reindex between write and validate."""
    project_path = Path(test_project.path)
    for name in ["Alice", "Bob"]:
        _write_schema_file(
            project_path,
            f"people/{name}.md",
            PERSON_NOTE.format(name=name, permalink=name.lower()),
        )
    await index_project()

    with pytest.raises(ToolError) as refused:
        await bm_schema(command="validate", note_type="person", project=test_project.name)
    assert "No Schema Found" in str(refused.value)

    # Follow the guidance: create the schema note with bm_write. No index_project() after this.
    await bm_write(
        identifier="schemas/person.md",
        content=(
            "---\n"
            "title: Person\n"
            "type: schema\n"
            "entity: person\n"
            "version: 1\n"
            "schema:\n"
            "  name: string\n"
            "  role?: string\n"
            "settings:\n"
            "  validation: warn\n"
            "---\n"
        ),
        project=test_project.name,
    )

    result = await bm_schema(command="validate", note_type="person", project=test_project.name)
    assert isinstance(result, str)
    assert "Schema Validation: person" in result


# --- Report rendering ---


@pytest.mark.asyncio
async def test_schema_validate_renders_warnings_and_errors(app, test_project, index_project):
    """A warn-mode schema reports warnings; a strict-mode schema reports errors."""
    project_path = Path(test_project.path)

    _write_schema_file(project_path, "schemas/Person.md", PERSON_SCHEMA)
    _write_schema_file(
        project_path,
        "schemas/Contract.md",
        """\
---
title: Contract
type: schema
entity: contract
version: 1
schema:
  party: string, counterparty
settings:
  validation: strict
---

# Contract
""",
    )
    # Neither note carries its schema's required field.
    _write_schema_file(
        project_path,
        "people/Nameless.md",
        """\
---
title: Nameless
type: person
permalink: people/nameless
---

# Nameless

## Observations
- [role] Engineer
""",
    )
    _write_schema_file(
        project_path,
        "contracts/Lease.md",
        """\
---
title: Lease
type: contract
permalink: contracts/lease
---

# Lease

## Observations
- [term] Two years
""",
    )

    await index_project()

    result = await bm_schema(command="validate", project=test_project.name)

    assert isinstance(result, str)
    assert "- **Nameless** — valid" in result
    assert "  - warning: " in result
    assert "- **Lease** — INVALID" in result
    assert "  - error: " in result


@pytest.mark.asyncio
async def test_schema_infer_reports_excluded_fields(app, test_project, index_project):
    """Fields below the threshold are listed as excluded in text and JSON output."""
    project_path = Path(test_project.path)

    for name in ["Alice", "Bob", "Charlie", "Dana", "Eve"]:
        _write_schema_file(
            project_path,
            f"people/{name}.md",
            PERSON_NOTE.format(name=name, permalink=name.lower()),
        )
    # One note in six carries a hobby, below the default 25% threshold.
    _write_schema_file(
        project_path,
        "people/Frank.md",
        """\
---
title: Frank
type: person
permalink: people/frank
---

# Frank

## Observations
- [name] Frank
- [hobby] Chess
""",
    )

    await index_project()

    text = await bm_schema(command="infer", note_type="person", project=test_project.name)
    assert isinstance(text, str)
    assert "## Suggested Schema" in text
    assert "## Excluded (below threshold)" in text
    assert "- hobby" in text

    data = await bm_schema(
        command="infer", note_type="person", project=test_project.name, output_format="json"
    )
    assert isinstance(data, dict)
    assert data["notes_analyzed"] == 6
    assert "hobby" in data["excluded"]


@pytest.mark.asyncio
async def test_schema_infer_no_notes_refuses(app, test_project, index_project):
    """Inferring a type with no notes refuses instead of returning an empty report."""
    with pytest.raises(ToolError) as refused:
        await bm_schema(command="infer", note_type="nobody", project=test_project.name)

    assert "No Notes Found of Type 'nobody'" in str(refused.value)


@pytest.mark.asyncio
async def test_schema_diff_renders_every_drift_kind(app, test_project, index_project):
    """New, dropped and cardinality drift each get their own section."""
    project_path = Path(test_project.path)

    _write_schema_file(
        project_path,
        "schemas/Person.md",
        """\
---
title: Person
type: schema
entity: person
version: 1
schema:
  name: string, full name
  role?: string, job title
  email?: string, contact address
settings:
  validation: warn
---

# Person
""",
    )
    # Every note repeats [role] (array usage for a single-value field), adds an
    # undeclared [hobby], and never carries the declared email.
    for name in ["Alice", "Bob"]:
        _write_schema_file(
            project_path,
            f"people/{name}.md",
            f"""\
---
title: {name}
type: person
permalink: people/{name.lower()}
---

# {name}

## Observations
- [name] {name}
- [role] Engineer
- [role] Manager
- [hobby] Chess
""",
        )

    await index_project()

    result = await bm_schema(command="diff", note_type="person", project=test_project.name)

    assert isinstance(result, str)
    assert "## New Fields (in notes but not in schema)" in result
    assert "**hobby**" in result
    assert "## Dropped Fields (in schema but rare in notes)" in result
    assert "**email**" in result
    assert "## Cardinality Changes" in result
    assert "role: schema declares single-value but usage is typically array" in result


@pytest.mark.asyncio
async def test_schema_diff_no_drift(app, test_project, index_project):
    """A schema that matches its notes reports no drift, in text and JSON."""
    project_path = Path(test_project.path)

    _write_schema_file(project_path, "schemas/Person.md", PERSON_SCHEMA)
    _write_schema_file(
        project_path,
        "people/Alice.md",
        PERSON_NOTE.format(name="Alice", permalink="alice"),
    )

    await index_project()

    text = await bm_schema(command="diff", note_type="person", project=test_project.name)
    assert isinstance(text, str)
    assert "No drift detected" in text

    data = await bm_schema(
        command="diff", note_type="person", project=test_project.name, output_format="json"
    )
    assert isinstance(data, dict)
    assert data["schema_found"] is True
    assert data["new_fields"] == []
    assert data["dropped_fields"] == []


@pytest.mark.asyncio
async def test_schema_diff_no_notes_refuses(app, test_project, index_project):
    """A schema with no notes of its type refuses: an empty comparison is not 'no drift'."""
    _write_schema_file(Path(test_project.path), "schemas/Meeting.md", MEETING_SCHEMA)

    await index_project()

    with pytest.raises(ToolError) as refused:
        await bm_schema(command="diff", note_type="meeting", project=test_project.name)

    assert "No Notes Found of Type 'meeting'" in str(refused.value)
