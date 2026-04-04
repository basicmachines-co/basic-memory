"""Telemetry coverage for entity service write/edit/reindex paths."""

from __future__ import annotations

import importlib
from contextlib import contextmanager

import pytest

from basic_memory.schemas import Entity as EntitySchema

entity_service_module = importlib.import_module("basic_memory.services.entity_service")


def _capture_spans():
    spans: list[tuple[str, dict]] = []

    @contextmanager
    def fake_span(name: str, **attrs):
        spans.append((name, attrs))
        yield

    return spans, fake_span


def _assert_names_in_order(names: list[str], expected: list[str]) -> None:
    cursor = 0
    for expected_name in expected:
        cursor = names.index(expected_name, cursor) + 1


@pytest.mark.asyncio
async def test_create_entity_emits_expected_phase_spans(entity_service, monkeypatch) -> None:
    spans, fake_span = _capture_spans()
    monkeypatch.setattr(entity_service_module.telemetry, "span", fake_span)

    schema = EntitySchema(
        title="Telemetry Create",
        directory="notes",
        note_type="note",
        content_type="text/markdown",
        content="Create telemetry content",
    )

    entity = await entity_service.create_entity(schema)

    assert entity.title == "Telemetry Create"
    span_names = [name for name, _ in spans]
    _assert_names_in_order(
        span_names,
        [
            "entity_service.create.resolve_permalink",
            "entity_service.create.write_file",
            "file_service.write",
            "entity_service.create.parse_markdown",
            "entity_service.create.upsert_entity",
            "entity_service.upsert.base_entity",
            "entity_service.upsert.relations",
            "entity_service.upsert.persist_checksum",
        ],
    )


@pytest.mark.asyncio
async def test_edit_entity_emits_expected_phase_spans(entity_service, monkeypatch) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Telemetry Edit",
            directory="notes",
            note_type="note",
            content_type="text/markdown",
            content="Before edit",
        )
    )

    spans, fake_span = _capture_spans()
    monkeypatch.setattr(entity_service_module.telemetry, "span", fake_span)

    updated = await entity_service.edit_entity(
        created.file_path,
        operation="append",
        content="\n\nAfter edit",
    )

    assert updated.id == created.id
    span_names = [name for name, _ in spans]
    _assert_names_in_order(
        span_names,
        [
            "entity_service.edit.resolve_entity",
            "entity_service.edit.read_file",
            "file_service.read",
            "entity_service.edit.apply_operation",
            "entity_service.edit.write_file",
            "file_service.write",
            "entity_service.edit.parse_markdown",
            "entity_service.edit.upsert_entity",
            "entity_service.upsert.base_entity",
            "entity_service.upsert.relations",
            "entity_service.upsert.persist_checksum",
        ],
    )


@pytest.mark.asyncio
async def test_reindex_entity_emits_expected_phase_spans(entity_service, monkeypatch) -> None:
    created = await entity_service.create_entity(
        EntitySchema(
            title="Telemetry Reindex",
            directory="notes",
            note_type="note",
            content_type="text/markdown",
            content="Reindex telemetry content",
        )
    )

    spans, fake_span = _capture_spans()
    monkeypatch.setattr(entity_service_module.telemetry, "span", fake_span)

    await entity_service.reindex_entity(created.id)

    span_names = [name for name, _ in spans]
    _assert_names_in_order(
        span_names,
        [
            "entity_service.reindex.load_entity",
            "entity_service.reindex.read_file",
            "file_service.read_content",
            "entity_service.reindex.parse_markdown",
            "entity_service.reindex.upsert_entity",
            "entity_service.upsert.base_entity",
            "entity_service.upsert.relations",
            "entity_service.upsert.hydrate_entity",
            "entity_service.reindex.update_checksum",
        ],
    )
    if entity_service.search_service is not None:
        assert "entity_service.reindex.search_index" in span_names
