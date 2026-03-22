"""Telemetry coverage for MCP tool root spans."""

from __future__ import annotations

import importlib
from contextlib import contextmanager

import pytest

build_context_module = importlib.import_module("basic_memory.mcp.tools.build_context")
edit_note_module = importlib.import_module("basic_memory.mcp.tools.edit_note")
read_note_module = importlib.import_module("basic_memory.mcp.tools.read_note")
search_module = importlib.import_module("basic_memory.mcp.tools.search")
write_note_module = importlib.import_module("basic_memory.mcp.tools.write_note")


def _recording_contexts():
    operations: list[tuple[str, dict]] = []
    contexts: list[dict] = []

    @contextmanager
    def fake_operation(name: str, **attrs):
        operations.append((name, attrs))
        yield

    @contextmanager
    def fake_contextualize(**attrs):
        contexts.append(attrs)
        yield

    return operations, contexts, fake_operation, fake_contextualize


def _contains_context(contexts: list[dict], expected: dict) -> bool:
    return any(expected.items() <= context.items() for context in contexts)


@pytest.mark.asyncio
async def test_write_note_emits_root_operation_and_project_context(
    app, test_project, monkeypatch
) -> None:
    operations, contexts, fake_operation, fake_contextualize = _recording_contexts()
    monkeypatch.setattr(write_note_module.telemetry, "operation", fake_operation)
    monkeypatch.setattr(write_note_module.telemetry, "contextualize", fake_contextualize)

    await write_note_module.write_note(
        project=test_project.name,
        title="Telemetry Note",
        directory="notes",
        content="Telemetry content",
        output_format="json",
    )

    assert operations == [
        (
            "mcp.tool.write_note",
            {
                "entrypoint": "mcp",
                "tool_name": "write_note",
                "requested_project": test_project.name,
                "workspace_id": None,
                "note_type": "note",
                "overwrite": False,
                "output_format": "json",
            },
        )
    ]
    assert _contains_context(
        contexts,
        {
            "project_name": test_project.name,
            "route_mode": "local_asgi",
        },
    )
    assert _contains_context(
        contexts,
        {
            "project_name": test_project.name,
            "tool_name": "write_note",
        },
    )


@pytest.mark.asyncio
async def test_read_note_emits_root_operation_and_project_context(
    app, test_project, monkeypatch
) -> None:
    await write_note_module.write_note(
        project=test_project.name,
        title="Readable Telemetry Note",
        directory="notes",
        content="Readable telemetry content",
    )

    operations, contexts, fake_operation, fake_contextualize = _recording_contexts()
    monkeypatch.setattr(read_note_module.telemetry, "operation", fake_operation)
    monkeypatch.setattr(read_note_module.telemetry, "contextualize", fake_contextualize)

    await read_note_module.read_note(
        "notes/readable-telemetry-note",
        project=test_project.name,
        output_format="json",
        include_frontmatter=True,
    )

    assert operations == [
        (
            "mcp.tool.read_note",
            {
                "entrypoint": "mcp",
                "tool_name": "read_note",
                "requested_project": test_project.name,
                "workspace_id": None,
                "output_format": "json",
                "page": 1,
                "page_size": 10,
                "include_frontmatter": True,
            },
        )
    ]
    assert _contains_context(
        contexts,
        {
            "project_name": test_project.name,
            "route_mode": "local_asgi",
        },
    )
    assert _contains_context(
        contexts,
        {
            "project_name": test_project.name,
            "tool_name": "read_note",
        },
    )


@pytest.mark.asyncio
async def test_search_notes_emits_root_operation_and_project_context(
    app, test_project, monkeypatch
) -> None:
    await write_note_module.write_note(
        project=test_project.name,
        title="Searchable Telemetry Note",
        directory="notes",
        content="Telemetry search content",
        tags=["telemetry"],
    )

    operations, contexts, fake_operation, fake_contextualize = _recording_contexts()
    monkeypatch.setattr(search_module.telemetry, "operation", fake_operation)
    monkeypatch.setattr(search_module.telemetry, "contextualize", fake_contextualize)

    await search_module.search_notes(
        project=test_project.name,
        query="telemetry",
        search_type="text",
        tags=["telemetry"],
        output_format="json",
    )

    assert operations[0] == (
        "mcp.tool.search_notes",
        {
            "entrypoint": "mcp",
            "tool_name": "search_notes",
            "requested_project": test_project.name,
            "workspace_id": None,
            "search_type": "text",
            "output_format": "json",
            "page": 1,
            "page_size": 10,
            "has_query": True,
            "note_type_filter_count": 0,
            "entity_type_filter_count": 0,
            "has_metadata_filters": False,
            "has_tags_filter": True,
            "has_status_filter": False,
        },
    )
    assert ("api.request.search",) == (operations[1][0],)
    assert _contains_context(
        contexts,
        {
            "project_name": test_project.name,
            "route_mode": "local_asgi",
        },
    )
    assert _contains_context(
        contexts,
        {
            "project_name": test_project.name,
            "tool_name": "search_notes",
        },
    )


@pytest.mark.asyncio
async def test_edit_note_emits_root_operation_and_project_context(
    app, test_project, monkeypatch
) -> None:
    await write_note_module.write_note(
        project=test_project.name,
        title="Editable Telemetry Note",
        directory="notes",
        content="Original telemetry content",
    )

    operations, contexts, fake_operation, fake_contextualize = _recording_contexts()
    monkeypatch.setattr(edit_note_module.telemetry, "operation", fake_operation)
    monkeypatch.setattr(edit_note_module.telemetry, "contextualize", fake_contextualize)

    await edit_note_module.edit_note(
        "notes/editable-telemetry-note",
        operation="append",
        content="\n\nAppended telemetry content",
        project=test_project.name,
        output_format="json",
    )

    assert operations == [
        (
            "mcp.tool.edit_note",
            {
                "entrypoint": "mcp",
                "tool_name": "edit_note",
                "requested_project": test_project.name,
                "workspace_id": None,
                "edit_operation": "append",
                "output_format": "json",
                "has_section": False,
                "has_find_text": False,
                "expected_replacements": 1,
            },
        )
    ]
    assert _contains_context(
        contexts,
        {
            "project_name": test_project.name,
            "route_mode": "local_asgi",
        },
    )
    assert _contains_context(
        contexts,
        {
            "project_name": test_project.name,
            "tool_name": "edit_note",
        },
    )


@pytest.mark.asyncio
async def test_build_context_emits_root_operation_and_project_context(
    app, test_project, monkeypatch
) -> None:
    await write_note_module.write_note(
        project=test_project.name,
        title="Context Telemetry Note",
        directory="notes",
        content="Context telemetry content",
    )

    operations, contexts, fake_operation, fake_contextualize = _recording_contexts()
    monkeypatch.setattr(build_context_module.telemetry, "operation", fake_operation)
    monkeypatch.setattr(build_context_module.telemetry, "contextualize", fake_contextualize)

    await build_context_module.build_context(
        url="memory://notes/context-telemetry-note",
        project=test_project.name,
        depth=2,
        timeframe="7d",
        page=1,
        page_size=5,
        max_related=3,
        output_format="json",
    )

    assert operations == [
        (
            "mcp.tool.build_context",
            {
                "entrypoint": "mcp",
                "tool_name": "build_context",
                "requested_project": test_project.name,
                "workspace_id": None,
                "depth": 2,
                "timeframe": "7d",
                "page": 1,
                "page_size": 5,
                "max_related": 3,
                "output_format": "json",
                "is_memory_url": True,
            },
        )
    ]
    assert _contains_context(
        contexts,
        {
            "project_name": test_project.name,
            "route_mode": "local_asgi",
        },
    )
    assert _contains_context(
        contexts,
        {
            "project_name": test_project.name,
            "tool_name": "build_context",
        },
    )
