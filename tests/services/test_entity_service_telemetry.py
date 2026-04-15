"""Telemetry coverage for the lower-level file spans used by entity service."""

from __future__ import annotations

import importlib
from contextlib import contextmanager

import pytest

from basic_memory.schemas import Entity as EntitySchema

telemetry_module = importlib.import_module("basic_memory.telemetry")


def _capture_spans():
    spans: list[tuple[str, dict]] = []

    @contextmanager
    def fake_span(name: str, **attrs):
        spans.append((name, attrs))
        yield

    return spans, fake_span


def _span_names(spans: list[tuple[str, dict]]) -> list[str]:
    return [name for name, _ in spans]


@pytest.mark.asyncio
async def test_create_entity_emits_file_write_span(entity_service, monkeypatch) -> None:
    spans, fake_span = _capture_spans()
    monkeypatch.setattr(telemetry_module, "span", fake_span)

    schema = EntitySchema(
        title="Telemetry Create",
        directory="notes",
        note_type="note",
        content_type="text/markdown",
        content="Create telemetry content",
    )

    entity = await entity_service.create_entity(schema)

    assert entity.title == "Telemetry Create"
    assert "file_service.write" in _span_names(spans)


@pytest.mark.asyncio
async def test_edit_entity_emits_file_read_and_write_spans(entity_service, monkeypatch) -> None:
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
    monkeypatch.setattr(telemetry_module, "span", fake_span)

    updated = await entity_service.edit_entity(
        created.file_path,
        operation="append",
        content="\n\nAfter edit",
    )

    assert updated.id == created.id
    span_names = _span_names(spans)
    assert "file_service.read" in span_names
    assert "file_service.write" in span_names
    assert span_names.index("file_service.read") < span_names.index("file_service.write")
