"""Tests for v2 schema router endpoints.

Tests the integration layer where ORM entities are converted to NoteData
and passed through the schema engine (infer, validate, diff).

Note: EntityType uses BeforeValidator(to_snake_case) so "Person" becomes "person"
in the database. All query params must use the stored (snake_case) form.
"""

from textwrap import dedent

import pytest
from httpx import AsyncClient

from basic_memory.models import Project
from basic_memory.schemas.base import Entity as EntitySchema


# --- Helpers ---


async def create_person_entities(entity_service, search_service):
    """Create multiple person entities with observations and relations for testing.

    Returns the created entities after indexing them for search.
    """
    persons = [
        EntitySchema(
            title="Alice",
            directory="people",
            note_type="person",
            content=dedent("""\
                ## Observations
                - [name] Alice Smith
                - [role] Engineer
                - [expertise] Python

                ## Relations
                - works_at [[Acme Corp]]
            """),
        ),
        EntitySchema(
            title="Bob",
            directory="people",
            note_type="person",
            content=dedent("""\
                ## Observations
                - [name] Bob Jones
                - [role] Designer
                - [expertise] UI/UX

                ## Relations
                - works_at [[Acme Corp]]
            """),
        ),
        EntitySchema(
            title="Carol",
            directory="people",
            note_type="person",
            content=dedent("""\
                ## Observations
                - [name] Carol Lee
                - [role] Manager

                ## Relations
                - works_at [[Globex]]
            """),
        ),
    ]

    created = []
    for schema in persons:
        entity, _ = await entity_service.create_or_update_entity(schema)
        await search_service.index_entity(entity)
        created.append(entity)

    return created


# --- Infer Endpoint Tests ---


@pytest.mark.asyncio
async def test_infer_schema(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service,
    search_service,
):
    """Infer a schema from person notes with observations and relations."""
    await create_person_entities(entity_service, search_service)

    response = await client.post(
        f"{v2_project_url}/schema/infer",
        params={"note_type": "person"},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["note_type"] == "person"
    assert data["notes_analyzed"] == 3
    assert isinstance(data["field_frequencies"], list)
    assert isinstance(data["suggested_schema"], dict)
    assert isinstance(data["suggested_required"], list)
    assert isinstance(data["suggested_optional"], list)

    # "name" and "role" appear in all 3 notes -> should be in frequencies
    freq_names = [f["name"] for f in data["field_frequencies"]]
    assert "name" in freq_names
    assert "role" in freq_names

    # "name" appears in 100% of notes -> should be suggested as required
    assert "name" in data["suggested_required"]
    assert "role" in data["suggested_required"]


@pytest.mark.asyncio
async def test_infer_schema_no_matching_notes(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Infer returns empty result when no notes of the given type exist."""
    response = await client.post(
        f"{v2_project_url}/schema/infer",
        params={"note_type": "nonexistent"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["notes_analyzed"] == 0
    assert data["field_frequencies"] == []
    assert data["suggested_schema"] == {}
    assert data["suggested_required"] == []
    assert data["suggested_optional"] == []


@pytest.mark.asyncio
async def test_infer_schema_includes_relations(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service,
    search_service,
):
    """Infer detects relation types as fields alongside observations."""
    await create_person_entities(entity_service, search_service)

    response = await client.post(
        f"{v2_project_url}/schema/infer",
        params={"note_type": "person"},
    )

    assert response.status_code == 200
    data = response.json()

    # "works_at" relation appears in all 3 notes -> should be in frequencies
    freq_names = [f["name"] for f in data["field_frequencies"]]
    assert "works_at" in freq_names

    # Check it's identified as a relation source
    works_at = next(f for f in data["field_frequencies"] if f["name"] == "works_at")
    assert works_at["source"] == "relation"
    assert works_at["count"] == 3


# --- Validate Endpoint Tests ---


@pytest.mark.asyncio
async def test_validate_with_inline_schema(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service,
    search_service,
):
    """Validate a person note against an inline schema in its entity_metadata."""
    # Create a person entity with an inline schema in entity_metadata.
    # The resolver will pick this up as resolution path 1 (inline schema).
    entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Dave",
            directory="people",
            note_type="person",
            entity_metadata={
                "schema": {"name": "string", "role": "string"},
            },
            content=dedent("""\
                ## Observations
                - [name] Dave Wilson
                - [role] Architect
            """),
        )
    )
    await search_service.index_entity(entity)

    response = await client.post(
        f"{v2_project_url}/schema/validate",
        params={"note_type": "person"},
    )

    assert response.status_code == 200
    data = response.json()

    # Entity was found and validated
    assert data["total_notes"] == 1
    assert isinstance(data["results"], list)
    assert len(data["results"]) == 1

    result = data["results"][0]
    assert result["passed"] is True
    assert result["schema_entity"] == "person"

    # Both "name" and "role" should be present
    field_statuses = {fr["field_name"]: fr["status"] for fr in result["field_results"]}
    assert field_statuses["name"] == "present"
    assert field_statuses["role"] == "present"


@pytest.mark.asyncio
async def test_validate_with_explicit_schema_reference_by_permalink_slug(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service,
    search_service,
):
    """Validate resolves explicit schema refs by exact schema identifier (non-fuzzy)."""
    schema_entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Strict Person V2",
            directory="schemas",
            note_type="schema",
            entity_metadata={
                "entity": "person",
                "schema": {"name": "string", "role": "string"},
            },
            content=dedent("""\
                ## Observations
                - [note] Strict schema for person notes
            """),
        )
    )
    await search_service.index_entity(schema_entity)

    note_entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Frank",
            directory="people",
            note_type="person",
            entity_metadata={
                # Explicit schema identifier does not equal entity metadata field ("person")
                # and must resolve by schema identifier, not fuzzy text search.
                "schema": "strict-person-v2",
            },
            content=dedent("""\
                ## Observations
                - [name] Frank Nguyen
                - [role] Engineer
            """),
        )
    )
    await search_service.index_entity(note_entity)

    response = await client.post(
        f"{v2_project_url}/schema/validate",
        params={"identifier": note_entity.permalink},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total_notes"] == 1
    assert len(data["results"]) == 1
    assert data["results"][0]["schema_entity"] == "person"
    assert data["results"][0]["passed"] is True


@pytest.mark.asyncio
async def test_validate_missing_required_field(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service,
    search_service,
):
    """Validate detects a missing required field and produces a warning."""
    # Schema requires "name" and "role", but note only has "name"
    entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Eve",
            directory="people",
            note_type="person",
            entity_metadata={
                "schema": {"name": "string", "role": "string"},
            },
            content=dedent("""\
                ## Observations
                - [name] Eve Martinez
            """),
        )
    )
    await search_service.index_entity(entity)

    response = await client.post(
        f"{v2_project_url}/schema/validate",
        params={"note_type": "person"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total_notes"] == 1

    result = data["results"][0]
    # Default validation mode is "warn" so missing required field produces warning, not error
    assert result["passed"] is True
    assert len(result["warnings"]) > 0
    assert any("role" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_validate_no_matching_notes(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Validate returns empty result when no notes of the given type exist."""
    response = await client.post(
        f"{v2_project_url}/schema/validate",
        params={"note_type": "nonexistent"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total_notes"] == 0
    assert data["total_entities"] == 0
    assert data["results"] == []


@pytest.mark.asyncio
async def test_validate_total_entities_without_schema(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service,
    search_service,
):
    """Validate reports total_entities even when no schema exists (total_notes == 0)."""
    await create_person_entities(entity_service, search_service)

    response = await client.post(
        f"{v2_project_url}/schema/validate",
        params={"note_type": "person"},
    )

    assert response.status_code == 200
    data = response.json()
    # No schema -> no notes validated
    assert data["total_notes"] == 0
    # But entities of this type do exist
    assert data["total_entities"] == 3
    assert data["results"] == []


# --- Frontmatter Validation Tests ---


@pytest.mark.asyncio
async def test_validate_with_frontmatter_rules_passes(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service,
    search_service,
):
    """Validate a note whose frontmatter matches settings.frontmatter rules."""
    # Schema note with frontmatter validation rules
    schema_entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Person Schema FM",
            directory="schemas",
            note_type="schema",
            entity_metadata={
                "entity": "person_fm",
                "schema": {"name": "string"},
                "settings": {
                    "validation": "warn",
                    "frontmatter": {
                        "tags?(array)": "string",
                        "status?(enum)": ["draft", "published"],
                    },
                },
            },
            content=dedent("""\
                ## Observations
                - [note] Schema with frontmatter rules
            """),
        )
    )
    await search_service.index_entity(schema_entity)

    # Note with matching frontmatter
    note_entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Grace",
            directory="people",
            note_type="person_fm",
            entity_metadata={
                "tags": ["engineer", "python"],
                "status": "published",
            },
            content=dedent("""\
                ## Observations
                - [name] Grace Hopper
            """),
        )
    )
    await search_service.index_entity(note_entity)

    response = await client.post(
        f"{v2_project_url}/schema/validate",
        params={"identifier": note_entity.permalink},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total_notes"] == 1
    result = data["results"][0]
    assert result["passed"] is True
    assert result["warnings"] == []
    # Should have field results for "name" (observation) + "tags" + "status" (frontmatter)
    field_names = [fr["field_name"] for fr in result["field_results"]]
    assert "tags" in field_names
    assert "status" in field_names


@pytest.mark.asyncio
async def test_validate_frontmatter_missing_required_key_warns(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service,
    search_service,
):
    """Missing required frontmatter key produces a warning."""
    schema_entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Person Schema FM2",
            directory="schemas",
            note_type="schema",
            entity_metadata={
                "entity": "person_fm2",
                "schema": {"name": "string"},
                "settings": {
                    "validation": "warn",
                    "frontmatter": {
                        "status": "string",  # required
                    },
                },
            },
            content=dedent("""\
                ## Observations
                - [note] Schema requiring status frontmatter
            """),
        )
    )
    await search_service.index_entity(schema_entity)

    # Note without the required "status" frontmatter key
    note_entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Hank",
            directory="people",
            note_type="person_fm2",
            content=dedent("""\
                ## Observations
                - [name] Hank Pym
            """),
        )
    )
    await search_service.index_entity(note_entity)

    response = await client.post(
        f"{v2_project_url}/schema/validate",
        params={"identifier": note_entity.permalink},
    )

    assert response.status_code == 200
    data = response.json()
    result = data["results"][0]
    assert result["passed"] is True
    assert any("status" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_validate_frontmatter_enum_mismatch_warns(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service,
    search_service,
):
    """Frontmatter enum mismatch produces a warning."""
    schema_entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Person Schema FM3",
            directory="schemas",
            note_type="schema",
            entity_metadata={
                "entity": "person_fm3",
                "schema": {"name": "string"},
                "settings": {
                    "validation": "warn",
                    "frontmatter": {
                        "status?(enum)": ["draft", "published"],
                    },
                },
            },
            content=dedent("""\
                ## Observations
                - [note] Schema with enum frontmatter
            """),
        )
    )
    await search_service.index_entity(schema_entity)

    # Note with invalid enum value in frontmatter
    note_entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Iris",
            directory="people",
            note_type="person_fm3",
            entity_metadata={
                "status": "archived",  # not in [draft, published]
            },
            content=dedent("""\
                ## Observations
                - [name] Iris West
            """),
        )
    )
    await search_service.index_entity(note_entity)

    response = await client.post(
        f"{v2_project_url}/schema/validate",
        params={"identifier": note_entity.permalink},
    )

    assert response.status_code == 200
    data = response.json()
    result = data["results"][0]
    assert result["passed"] is True
    # Should have a warning about the enum mismatch
    assert any("status" in w for w in result["warnings"])
    # The field result should show enum_mismatch
    status_fr = next(fr for fr in result["field_results"] if fr["field_name"] == "status")
    assert status_fr["status"] == "enum_mismatch"


# --- Diff Endpoint Tests ---


@pytest.mark.asyncio
async def test_diff_no_schema_found(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
):
    """Diff returns empty drift report when no schema exists for the type."""
    response = await client.get(
        f"{v2_project_url}/schema/diff/nonexistent",
    )

    assert response.status_code == 200
    data = response.json()
    assert data["note_type"] == "nonexistent"
    assert data["new_fields"] == []
    assert data["dropped_fields"] == []
    assert data["cardinality_changes"] == []


@pytest.mark.asyncio
async def test_diff_with_schema_note(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service,
    search_service,
):
    """Diff detects new fields and drift when a schema note exists."""
    # Create a schema note (type="schema" with entity/schema metadata).
    # The search_fn in the router looks for schema notes by searching
    # with types=["schema"] and then returns row.metadata from the search index.
    schema_entity, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Person Schema",
            directory="schemas",
            note_type="schema",
            entity_metadata={
                "entity": "person",
                "version": 1,
                "schema": {"name": "string", "role": "string"},
            },
            content=dedent("""\
                ## Observations
                - [note] Schema definition for person entities
            """),
        )
    )
    await search_service.index_entity(schema_entity)

    # Create person entities with extra fields not in schema
    await create_person_entities(entity_service, search_service)

    response = await client.get(
        f"{v2_project_url}/schema/diff/person",
    )

    assert response.status_code == 200
    data = response.json()
    assert data["note_type"] == "person"
    assert isinstance(data["new_fields"], list)
    assert isinstance(data["dropped_fields"], list)
    assert isinstance(data["cardinality_changes"], list)
