"""Tests proving upsert_entity_from_markdown optimizations.

Verifies that:
1. Redundant get_by_file_path call is eliminated (entity passed directly)
2. Final reload uses find_by_ids (PK lookup) instead of get_by_file_path (string lookup)
3. Telemetry sub-spans are emitted for each DB phase
4. Correctness is preserved for create, update, and edit flows
"""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from basic_memory.markdown.schemas import (
    EntityFrontmatter,
    EntityMarkdown,
    Observation as MarkdownObservation,
    Relation as MarkdownRelation,
)
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.services.entity_service import EntityService

entity_service_module = importlib.import_module("basic_memory.services.entity_service")


# --- Helpers ---


def _make_markdown(
    title: str = "Test Entity",
    observations: list | None = None,
    relations: list | None = None,
) -> EntityMarkdown:
    frontmatter = EntityFrontmatter(metadata={"title": title, "type": "note"})
    return EntityMarkdown(
        frontmatter=frontmatter,
        observations=observations or [],
        relations=relations or [],
        created=datetime.now(timezone.utc),
        modified=datetime.now(timezone.utc),
    )


def _capture_spans():
    spans: list[tuple[str, dict]] = []

    @contextmanager
    def fake_span(name: str, **attrs):
        spans.append((name, attrs))
        yield

    return spans, fake_span


# --- Optimization 1: No redundant get_by_file_path in update_entity_relations ---


@pytest.mark.asyncio
async def test_upsert_update_does_not_refetch_entity(entity_service: EntityService, monkeypatch):
    """update_entity_relations should NOT call get_by_file_path — entity is passed directly."""
    # Create an entity first
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Refetch Test",
            directory="notes",
            note_type="note",
            content="# Refetch Test\n\n## Observations\n- [fact] some fact",
        )
    )

    # Spy on get_by_file_path calls
    original_get_by_file_path = entity_service.repository.get_by_file_path
    call_count = 0

    async def spy_get_by_file_path(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return await original_get_by_file_path(*args, **kwargs)

    monkeypatch.setattr(entity_service.repository, "get_by_file_path", spy_get_by_file_path)

    # Run upsert with is_new=False — this calls update_entity_and_observations + update_entity_relations
    markdown = _make_markdown(
        title="Refetch Test",
        observations=[MarkdownObservation(content="updated fact", category="fact")],
    )
    await entity_service.upsert_entity_from_markdown(Path(entity.file_path), markdown, is_new=False)

    # update_entity_and_observations calls get_by_file_path once (to load the entity)
    # update_entity_relations should NOT call it at all (entity passed directly)
    assert call_count == 1, (
        f"Expected 1 get_by_file_path call (in update_entity_and_observations only), "
        f"got {call_count}. update_entity_relations should not re-fetch."
    )


# --- Optimization 2: Final reload uses find_by_ids (PK) not get_by_file_path ---


@pytest.mark.asyncio
async def test_update_entity_relations_uses_pk_reload(entity_service: EntityService, monkeypatch):
    """update_entity_relations should use find_by_ids for the final reload, not get_by_file_path."""
    entity = await entity_service.create_entity(
        EntitySchema(
            title="PK Reload Test",
            directory="notes",
            note_type="note",
            content="# PK Reload Test",
        )
    )

    # Spy on find_by_ids calls
    original_find_by_ids = entity_service.repository.find_by_ids
    find_by_ids_calls = []

    async def spy_find_by_ids(ids):
        find_by_ids_calls.append(ids)
        return await original_find_by_ids(ids)

    monkeypatch.setattr(entity_service.repository, "find_by_ids", spy_find_by_ids)

    markdown = _make_markdown(title="PK Reload Test")
    await entity_service.upsert_entity_from_markdown(Path(entity.file_path), markdown, is_new=False)

    # update_entity_relations should call find_by_ids once with the entity's PK
    assert len(find_by_ids_calls) == 1
    assert find_by_ids_calls[0] == [entity.id]


@pytest.mark.asyncio
async def test_create_or_update_entity_uses_lightweight_exact_resolution(
    entity_service: EntityService, monkeypatch
):
    """create_or_update_entity should use strict lookups without eager relation loading."""
    schema = EntitySchema(
        title="Create Or Update",
        directory="notes",
        note_type="note",
        content="# Create Or Update",
    )
    sentinel_entity = SimpleNamespace(file_path="notes/existing.md")
    resolve_calls: list[tuple[str, dict]] = []

    async def fake_resolve_link(link_text: str, **kwargs):
        resolve_calls.append((link_text, kwargs))
        if link_text == schema.file_path:
            return None
        return sentinel_entity

    monkeypatch.setattr(entity_service.link_resolver, "resolve_link", fake_resolve_link)
    monkeypatch.setattr(entity_service, "update_entity", AsyncMock(return_value=sentinel_entity))

    entity, is_new = await entity_service.create_or_update_entity(schema)

    assert entity is sentinel_entity
    assert is_new is False
    assert resolve_calls == [
        (schema.file_path, {"strict": True, "load_relations": False}),
        (schema.permalink, {"strict": True, "load_relations": False}),
    ]


# --- Telemetry sub-spans ---


@pytest.mark.asyncio
async def test_upsert_update_emits_sub_spans(entity_service: EntityService, monkeypatch):
    """upsert_entity_from_markdown (update path) should emit sub-spans for each DB phase."""
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Span Test",
            directory="notes",
            note_type="note",
            content="# Span Test\n\n## Observations\n- [fact] original",
        )
    )

    spans, fake_span = _capture_spans()
    monkeypatch.setattr(entity_service_module.telemetry, "span", fake_span)

    markdown = _make_markdown(
        title="Span Test",
        observations=[MarkdownObservation(content="updated", category="fact")],
    )
    await entity_service.upsert_entity_from_markdown(Path(entity.file_path), markdown, is_new=False)

    span_names = [name for name, _ in spans]

    # update_entity_and_observations sub-spans
    assert "upsert.update.fetch_entity" in span_names
    assert "upsert.update.delete_observations" in span_names
    assert "upsert.update.insert_observations" in span_names
    assert "upsert.update.save_entity" in span_names

    # update_entity_relations sub-spans
    assert "upsert.relations.delete_existing" in span_names
    assert "upsert.relations.reload_entity" in span_names


@pytest.mark.asyncio
async def test_upsert_with_relations_emits_resolve_and_insert_spans(
    entity_service: EntityService, monkeypatch
):
    """When relations exist, resolve_links and insert_relations spans should be emitted."""
    # Create two entities so the relation can resolve
    await entity_service.create_entity(
        EntitySchema(
            title="Target Entity",
            directory="notes",
            note_type="note",
            content="# Target Entity",
        )
    )
    source = await entity_service.create_entity(
        EntitySchema(
            title="Source Entity",
            directory="notes",
            note_type="note",
            content="# Source Entity",
        )
    )

    spans, fake_span = _capture_spans()
    monkeypatch.setattr(entity_service_module.telemetry, "span", fake_span)

    markdown = _make_markdown(
        title="Source Entity",
        relations=[MarkdownRelation(type="links_to", target="Target Entity")],
    )
    await entity_service.upsert_entity_from_markdown(Path(source.file_path), markdown, is_new=False)

    span_names = [name for name, _ in spans]
    assert "upsert.relations.resolve_links" in span_names
    assert "upsert.relations.insert_relations" in span_names


@pytest.mark.asyncio
async def test_upsert_with_relations_uses_lightweight_exact_resolution(
    entity_service: EntityService, monkeypatch
):
    """Relation target resolution should skip eager loading during upsert."""
    target = await entity_service.create_entity(
        EntitySchema(
            title="Lightweight Target",
            directory="notes",
            note_type="note",
            content="# Lightweight Target",
        )
    )
    source = await entity_service.create_entity(
        EntitySchema(
            title="Lightweight Source",
            directory="notes",
            note_type="note",
            content="# Lightweight Source",
        )
    )
    resolve_calls: list[tuple[str, dict]] = []

    async def fake_resolve_link(link_text: str, **kwargs):
        resolve_calls.append((link_text, kwargs))
        return target

    monkeypatch.setattr(entity_service.link_resolver, "resolve_link", fake_resolve_link)

    markdown = _make_markdown(
        title="Lightweight Source",
        relations=[MarkdownRelation(type="links_to", target="Lightweight Target")],
    )
    await entity_service.upsert_entity_from_markdown(Path(source.file_path), markdown, is_new=False)

    assert resolve_calls == [
        ("Lightweight Target", {"strict": True, "load_relations": False}),
    ]


# --- Correctness: full round-trip ---


@pytest.mark.asyncio
async def test_upsert_update_preserves_observations(entity_service: EntityService):
    """After upsert (update path), observations should be correctly replaced."""
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Obs Test",
            directory="notes",
            note_type="note",
            content="# Obs Test\n\n## Observations\n- [fact] original fact",
        )
    )
    assert len(entity.observations) == 1

    markdown = _make_markdown(
        title="Obs Test",
        observations=[
            MarkdownObservation(content="new fact 1", category="fact"),
            MarkdownObservation(content="new fact 2", category="idea"),
        ],
    )
    updated = await entity_service.upsert_entity_from_markdown(
        Path(entity.file_path), markdown, is_new=False
    )

    assert updated.id == entity.id
    assert len(updated.observations) == 2
    obs_contents = {o.content for o in updated.observations}
    assert obs_contents == {"new fact 1", "new fact 2"}


@pytest.mark.asyncio
async def test_upsert_update_preserves_relations(entity_service: EntityService):
    """After upsert (update path), relations should be correctly replaced."""
    target = await entity_service.create_entity(
        EntitySchema(
            title="Relation Target",
            directory="notes",
            note_type="note",
            content="# Relation Target",
        )
    )
    source = await entity_service.create_entity(
        EntitySchema(
            title="Relation Source",
            directory="notes",
            note_type="note",
            content="# Relation Source\n\n## Relations\n- links_to [[Relation Target]]",
        )
    )
    assert len(source.relations) == 1

    markdown = _make_markdown(
        title="Relation Source",
        relations=[MarkdownRelation(type="references", target="Relation Target")],
    )
    updated = await entity_service.upsert_entity_from_markdown(
        Path(source.file_path), markdown, is_new=False
    )

    assert updated.id == source.id
    # Old relation replaced with new one
    outgoing = [r for r in updated.relations if r.from_id == source.id]
    assert len(outgoing) == 1
    assert outgoing[0].relation_type == "references"
    assert outgoing[0].to_id == target.id


@pytest.mark.asyncio
async def test_upsert_create_path_works(entity_service: EntityService):
    """The is_new=True path should still work correctly."""
    markdown = _make_markdown(
        title="Create Path Test",
        observations=[MarkdownObservation(content="a fact", category="fact")],
    )
    result = await entity_service.upsert_entity_from_markdown(
        Path("notes/create-path-test.md"), markdown, is_new=True
    )

    assert result.title == "Create Path Test"
    assert len(result.observations) == 1
    assert result.observations[0].content == "a fact"


@pytest.mark.asyncio
async def test_edit_entity_end_to_end(entity_service: EntityService):
    """Full edit_entity flow uses optimized upsert and returns correct entity."""
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
    # Checksum should be set (not None) after edit completes
    assert updated.checksum is not None


@pytest.mark.asyncio
async def test_edit_entity_uses_lightweight_identifier_resolution(
    entity_service: EntityService, monkeypatch
):
    """edit_entity should resolve the target note without eager relation loading."""
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Edit Lightweight",
            directory="notes",
            note_type="note",
            content="# Edit Lightweight\n\nOriginal content.",
        )
    )
    original_resolve_link = entity_service.link_resolver.resolve_link
    resolve_calls: list[tuple[str, dict]] = []

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
