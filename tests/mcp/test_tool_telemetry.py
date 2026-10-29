"""Telemetry coverage for MCP tool root spans."""

from __future__ import annotations

import importlib
from contextlib import contextmanager

import logfire
import pytest
from typing import Any

from basic_memory.mcp.tools import bm_context, bm_write

edit_note_module = importlib.import_module("basic_memory.mcp.tools.edit_note")
read_note_module = importlib.import_module("basic_memory.mcp.tools.read_note")
search_module = importlib.import_module("basic_memory.mcp.tools.search")


class _NoopSpan:
    """Minimal stand-in for a live logfire span during tests."""

    def set_attributes(self, attrs: dict[str, Any]) -> None:
        pass

    def set_attribute(self, key: str, value) -> None:
        pass


def _recording_spans():
    spans: list[tuple[str, dict[str, Any]]] = []

    @contextmanager
    def fake_span(name: str, **attrs):
        spans.append((name, attrs))
        yield _NoopSpan()

    return spans, fake_span


def _contains_span_attrs(
    spans: list[tuple[str, dict[str, Any]]], name: str, expected: dict[str, Any]
) -> bool:
    return any(
        span_name == name and expected.items() <= attrs.items() for span_name, attrs in spans
    )


@pytest.mark.asyncio
async def test_read_note_emits_root_operation_and_project_context(
    app, test_project, monkeypatch
) -> None:
    await bm_write(
        project=test_project.name,
        title="Readable Telemetry Note",
        directory="notes",
        content="Readable telemetry content",
    )

    spans, fake_span = _recording_spans()
    monkeypatch.setattr(logfire, "span", fake_span)

    await read_note_module.read_note(
        "notes/readable-telemetry-note",
        project=test_project.name,
        output_format="json",
        include_frontmatter=True,
    )

    assert spans[0] == (
        "mcp.tool.read_note",
        {
            "entrypoint": "mcp",
            "tool_name": "read_note",
            "requested_project": test_project.name,
            "requested_project_id": None,
            "page": 1,
            "page_size": 10,
            "output_format": "json",
            "include_frontmatter": True,
            "include_content": True,
        },
    )
    span_names = [name for name, _ in spans]
    assert "api.request.knowledge.resolve_entity" in span_names
    assert "api.request.knowledge.get_entity" in span_names
    assert "api.request.resource.get_content" not in span_names
    assert _contains_span_attrs(
        spans,
        "routing.client_session",
        {
            "project_name": test_project.name,
            "route_mode": "local_asgi",
        },
    )


@pytest.mark.asyncio
async def test_search_notes_emits_root_operation_and_project_context(
    app, test_project, monkeypatch
) -> None:
    await bm_write(
        project=test_project.name,
        title="Searchable Telemetry Note",
        directory="notes",
        content="Telemetry search content",
        tags=["telemetry"],
    )

    spans, fake_span = _recording_spans()
    monkeypatch.setattr(logfire, "span", fake_span)

    await search_module.search_notes(
        project=test_project.name,
        query="telemetry",
        search_type="text",
        tags=["telemetry"],
        output_format="json",
    )

    assert spans[0] == (
        "mcp.tool.search_notes",
        {
            "entrypoint": "mcp",
            "tool_name": "search_notes",
            "requested_project": test_project.name,
            "requested_project_id": None,
            "search_all_projects": False,
            "search_type": "text",
            "output_format": "json",
            "page": 1,
            "page_size": 10,
            "has_query": True,
            "note_type_filter_count": 0,
            "entity_type_filter_count": 0,
            "category_filter_count": 0,
            "has_filters": True,
            "has_tags_filter": True,
            "has_status_filter": False,
            "has_temporal_filter": False,
        },
    )
    span_names = [name for name, _ in spans]
    assert "api.request.search" in span_names
    assert _contains_span_attrs(
        spans,
        "routing.client_session",
        {
            "project_name": test_project.name,
            "route_mode": "local_asgi",
        },
    )


@pytest.mark.asyncio
async def test_edit_note_emits_root_operation_and_project_context(
    app, test_project, monkeypatch
) -> None:
    await bm_write(
        project=test_project.name,
        title="Editable Telemetry Note",
        directory="notes",
        content="Original telemetry content",
    )

    spans, fake_span = _recording_spans()
    monkeypatch.setattr(logfire, "span", fake_span)

    await edit_note_module.edit_note(
        "notes/editable-telemetry-note",
        operation="append",
        content="\n\nAppended telemetry content",
        project=test_project.name,
        output_format="json",
    )

    assert spans[0] == (
        "mcp.tool.edit_note",
        {
            "entrypoint": "mcp",
            "tool_name": "edit_note",
            "requested_project": test_project.name,
            "requested_project_id": None,
            "edit_operation": "append",
            "output_format": "json",
            "has_section": False,
            "has_find_text": False,
            "expected_replacements": 1,
            "replace_subsections": True,
            "has_metadata": False,
            "has_expected_checksum": False,
        },
    )
    span_names = [name for name, _ in spans]
    assert "api.request.knowledge.resolve_entity" in span_names
    assert "api.request.knowledge.edit_entity" in span_names
    assert _contains_span_attrs(
        spans,
        "routing.client_session",
        {
            "project_name": test_project.name,
            "route_mode": "local_asgi",
        },
    )


@pytest.mark.asyncio
async def test_bm_context_emits_client_operation_and_project_context(
    app, test_project, monkeypatch
) -> None:
    await bm_write(
        project=test_project.name,
        title="Context Telemetry Note",
        directory="notes",
        content="Context telemetry content",
    )

    spans, fake_span = _recording_spans()
    monkeypatch.setattr(logfire, "span", fake_span)

    await bm_context(
        identifier="memory://notes/context-telemetry-note",
        project=test_project.name,
        depth=2,
        since="7d",
        page=1,
        page_size=5,
        max_related=3,
        output_format="json",
    )

    assert _contains_span_attrs(
        spans,
        "mcp.client.memory.build_context",
        {"client_name": "memory", "operation": "build_context", "page": 1, "page_size": 5},
    )
    span_names = [name for name, _ in spans]
    assert "api.request.memory.build_context" in span_names
    assert _contains_span_attrs(
        spans,
        "routing.client_session",
        {
            "project_name": test_project.name,
            "route_mode": "local_asgi",
        },
    )
