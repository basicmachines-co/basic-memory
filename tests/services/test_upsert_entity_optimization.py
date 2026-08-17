"""Tests for the generation-safe EntityService write compatibility surface."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from basic_memory import db
from basic_memory.models import NoteContent, Relation
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.services.entity_service import EntityService


@pytest.mark.asyncio
async def test_create_or_update_entity_uses_lightweight_exact_resolution(
    entity_service: EntityService,
    monkeypatch,
) -> None:
    """Compatibility writes resolve exact file/permalink identities without graph loading."""
    schema = EntitySchema(
        title="Create Or Update",
        directory="notes",
        note_type="note",
        content="# Create Or Update",
    )
    sentinel_entity = SimpleNamespace(file_path="notes/existing.md")
    repository_calls: list[tuple[str, str, dict[str, Any]]] = []

    async def fake_get_by_file_path(session, file_path: str, **kwargs):
        repository_calls.append(("file_path", file_path, kwargs))
        return None

    async def fake_get_by_permalink(session, permalink: str, **kwargs):
        repository_calls.append(("permalink", permalink, kwargs))
        return sentinel_entity

    monkeypatch.setattr(entity_service.repository, "get_by_file_path", fake_get_by_file_path)
    monkeypatch.setattr(entity_service.repository, "get_by_permalink", fake_get_by_permalink)
    monkeypatch.setattr(
        entity_service.link_resolver,
        "resolve_link",
        AsyncMock(side_effect=AssertionError("canonical writes must not use link aliases")),
    )
    monkeypatch.setattr(entity_service, "update_entity", AsyncMock(return_value=sentinel_entity))

    entity, is_new = await entity_service.create_or_update_entity(schema)

    assert entity is sentinel_entity
    assert is_new is False
    assert repository_calls == [
        ("file_path", schema.file_path, {"load_relations": False}),
        ("permalink", schema.permalink, {"load_relations": False}),
    ]


@pytest.mark.asyncio
async def test_entity_service_writes_publish_relations_by_note_generation(
    entity_service: EntityService,
    session_maker,
) -> None:
    """The retained service surface cannot create generation-zero relation rows."""
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Generation Source",
            directory="notes",
            note_type="note",
            content=("# Generation Source\n\n## Relations\n- documents [[First Target]]\n"),
        )
    )

    async with db.scoped_session(session_maker) as session:
        note_content = await session.get(NoteContent, entity.id)
        first_rows = list(
            (await session.execute(select(Relation).where(Relation.from_id == entity.id)))
            .scalars()
            .all()
        )

    assert note_content is not None
    assert note_content.db_version == 1
    assert [(row.to_name, row.to_id, row.generation) for row in first_rows] == [
        ("First Target", None, 1)
    ]

    await entity_service.update_entity(
        entity,
        EntitySchema(
            title="Generation Source",
            directory="notes",
            note_type="note",
            content=("# Generation Source\n\n## Relations\n- documents [[Second Target]]\n"),
        ),
    )

    async with db.scoped_session(session_maker) as session:
        note_content = await session.get(NoteContent, entity.id)
        second_rows = list(
            (await session.execute(select(Relation).where(Relation.from_id == entity.id)))
            .scalars()
            .all()
        )

    assert note_content is not None
    assert note_content.db_version == 2
    assert [(row.to_name, row.to_id, row.generation) for row in second_rows] == [
        ("Second Target", None, 2)
    ]


@pytest.mark.asyncio
async def test_edit_entity_end_to_end(entity_service: EntityService) -> None:
    """Compatibility edits still rebuild observations and finish the checksum."""
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Edit E2E",
            directory="notes",
            note_type="note",
            content="# Edit E2E\n\nOriginal content.",
        )
    )

    updated = await entity_service.edit_entity(
        entity.file_path,
        operation="append",
        content="\n\n## Observations\n- [fact] appended fact",
    )

    assert updated.id == entity.id
    assert len(updated.observations) == 1
    assert updated.observations[0].content == "appended fact"
    assert updated.checksum is not None


@pytest.mark.asyncio
async def test_edit_entity_uses_lightweight_identifier_resolution(
    entity_service: EntityService,
    monkeypatch,
) -> None:
    """Compatibility edits resolve the note without eager relation loading."""
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Edit Lightweight",
            directory="notes",
            note_type="note",
            content="# Edit Lightweight\n\nOriginal content.",
        )
    )
    original_resolve_link = entity_service.link_resolver.resolve_link
    resolve_calls: list[tuple[str, dict[str, Any]]] = []

    async def spy_resolve_link(link_text: str, **kwargs):
        resolve_calls.append((link_text, kwargs))
        return await original_resolve_link(link_text, **kwargs)

    monkeypatch.setattr(entity_service.link_resolver, "resolve_link", spy_resolve_link)

    await entity_service.edit_entity(
        entity.file_path,
        operation="append",
        content="\n\nNo relation changes here.",
    )

    assert resolve_calls[0] == (
        entity.file_path,
        {"strict": True, "load_relations": False},
    )
