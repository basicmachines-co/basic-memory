"""Schema identity must describe the definition the real API validated."""

from collections.abc import Generator, Sequence
from typing import Literal, override

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from basic_memory.deps.services import get_schema_validation_observer
from basic_memory.models import Project
from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.services.entity_service import EntityService
from basic_memory.services.schema_validation_hooks import (
    SchemaValidationObserver,
    ValidatedNoteOutcome,
)
from basic_memory.services.search_service import SearchService


class IdentityObserver(SchemaValidationObserver):
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[ValidatedNoteOutcome, ...]]] = []

    @override
    async def on_notes_validated(
        self, *, project_external_id: str, outcomes: Sequence[ValidatedNoteOutcome]
    ) -> None:
        self.calls.append((project_external_id, tuple(outcomes)))


@pytest.fixture
def identity_observer(app: FastAPI) -> Generator[IdentityObserver, None, None]:
    observer = IdentityObserver()
    app.dependency_overrides[get_schema_validation_observer] = lambda: observer
    yield observer
    app.dependency_overrides.pop(get_schema_validation_observer, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["identifier", "type", "all"])
@pytest.mark.parametrize("reference", ["explicit", "implicit", "missing", "inline"])
async def test_validation_identity_matches_definition_in_every_scope(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service: EntityService,
    search_service: SearchService,
    identity_observer: IdentityObserver,
    scope: Literal["identifier", "type", "all"],
    reference: Literal["explicit", "implicit", "missing", "inline"],
) -> None:
    # Both schemas cover person, but their required fields differ. Whichever
    # database row is selected, the API report independently identifies its
    # definition; a guessed id from the other candidate fails this assertion.
    schema_fields: dict[str, str] = {}
    explicit_title = "person-with-role"
    for title, field in (("person-with-name", "name"), (explicit_title, "role")):
        schema, _ = await entity_service.create_or_update_entity(
            EntitySchema(
                title=title,
                directory="schemas",
                note_type="schema",
                entity_metadata={
                    "entity": "person",
                    "schema": {field: "string"},
                    "settings": {"validation": "strict"},
                },
                content="A person schema.\n",
            )
        )
        await search_service.index_entity(schema)
        schema_fields[schema.external_id] = field

    match reference:
        case "explicit":
            metadata = {"schema": explicit_title}
        case "missing":
            metadata = {"schema": "missing-schema-reference"}
        case "inline":
            metadata = {"schema": {"inline_field": "string"}}
        case "implicit":
            metadata = {}

    note, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Identity example",
            directory="people",
            note_type="person",
            entity_metadata=metadata,
            content="## Observations\n- [name] Example\n- [inline_field] Present\n",
        )
    )
    await search_service.index_entity(note)
    match scope:
        case "identifier":
            params = {"identifier": note.permalink}
        case "type":
            params = {"note_type": "person"}
        case "all":
            params = {}

    response = await client.post(f"{v2_project_url}/schema/validate", params=params)
    assert response.status_code == 200
    report = response.json()
    assert len(identity_observer.calls) == 1
    project_id, outcomes = identity_observer.calls[0]
    assert project_id == test_project.external_id
    assert len(outcomes) == len(report["results"]) == 1
    outcome = outcomes[0]
    assert outcome.note_external_id == note.external_id
    assert outcome.passed == report["results"][0]["passed"]
    fields = [field["field_name"] for field in report["results"][0]["field_results"]]
    if reference == "inline":
        assert outcome.schema_kind == "inline"
        assert outcome.schema_external_id == note.external_id
        assert fields == ["inline_field"]
    else:
        assert outcome.schema_kind == "named"
        assert outcome.schema_external_id in schema_fields
        assert fields == [schema_fields[outcome.schema_external_id]]
        if reference == "explicit":
            assert fields == ["role"]
            assert outcome.passed is False
        if reference == "missing":
            assert outcome.schema_reference == "missing-schema-reference"

    # Identity is for observers; the public validation response is unchanged.
    assert "schema_external_id" not in report["results"][0]
    assert "schema_kind" not in report["results"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["identifier", "type", "all"])
async def test_invalid_schema_never_reports_an_identity(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service: EntityService,
    search_service: SearchService,
    identity_observer: IdentityObserver,
    scope: str,
) -> None:
    note, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Invalid schema example",
            directory="people",
            note_type="person",
            entity_metadata={
                "schema": {"name": "string"},
                "settings": {"validation": "invalid-mode"},
            },
            content="## Observations\n- [name] Example\n",
        )
    )
    await search_service.index_entity(note)
    params = (
        {"identifier": note.permalink}
        if scope == "identifier"
        else {"note_type": "person"}
        if scope == "type"
        else {}
    )
    response = await client.post(f"{v2_project_url}/schema/validate", params=params)
    assert response.status_code == 400
    assert identity_observer.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["type", "all"])
async def test_mixed_batch_attributes_each_note_to_its_own_schema(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    entity_service: EntityService,
    search_service: SearchService,
    identity_observer: IdentityObserver,
    scope: str,
) -> None:
    # A passing named note, a failing named note, and an inline note must
    # retain their own identities and results regardless of batch ordering.
    expected: dict[str, tuple[str, str, bool]] = {}
    for field, passed in (("name", True), ("role", False)):
        schema, _ = await entity_service.create_or_update_entity(
            EntitySchema(
                title=f"person-by-{field}",
                directory="schemas",
                note_type="schema",
                entity_metadata={
                    "entity": "person",
                    "schema": {field: "string"},
                    "settings": {"validation": "strict"},
                },
                content="A person schema.\n",
            )
        )
        await search_service.index_entity(schema)
        note, _ = await entity_service.create_or_update_entity(
            EntitySchema(
                title=f"Person with {field} schema",
                directory="people",
                note_type="person",
                entity_metadata={"schema": schema.title},
                content="## Observations\n- [name] Example\n",
            )
        )
        await search_service.index_entity(note)
        expected[note.external_id] = (schema.external_id, "named", passed)

    inline, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Person with inline schema",
            directory="people",
            note_type="person",
            entity_metadata={"schema": {"name": "string"}},
            content="## Observations\n- [name] Example\n",
        )
    )
    await search_service.index_entity(inline)
    expected[inline.external_id] = (inline.external_id, "inline", True)

    params = {"note_type": "person"} if scope == "type" else {}
    response = await client.post(f"{v2_project_url}/schema/validate", params=params)
    assert response.status_code == 200
    assert response.json()["total_notes"] == 3
    assert response.json()["valid_count"] == 2
    assert len(identity_observer.calls) == 1
    project_id, outcomes = identity_observer.calls[0]
    assert project_id == test_project.external_id
    assert len(outcomes) == 3
    assert {
        outcome.note_external_id: (outcome.schema_external_id, outcome.schema_kind, outcome.passed)
        for outcome in outcomes
    } == expected
