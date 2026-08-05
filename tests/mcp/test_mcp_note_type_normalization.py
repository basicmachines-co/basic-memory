"""MCP boundary coverage for note-type canonicalization."""

from contextlib import asynccontextmanager
from typing import Any

import pytest

from basic_memory.schemas.search import SearchResponse


@pytest.mark.asyncio
async def test_search_notes_canonicalizes_multiword_note_types(monkeypatch):
    """MCP filters use snake_case rather than lowercase-only normalization."""
    import importlib

    search_module = importlib.import_module("basic_memory.mcp.tools.search")
    clients_module = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield object(), StubProject()

    async def fake_resolve_project_and_path(
        client, identifier, project=None, context=None, headers=None
    ):
        return StubProject(), identifier, False

    captured_payload: dict[str, Any] = {}

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    monkeypatch.setattr(search_module, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(
        search_module,
        "resolve_project_and_path",
        fake_resolve_project_and_path,
    )
    monkeypatch.setattr(clients_module, "SearchClient", MockSearchClient)

    await search_module.search_notes(
        project="test-project",
        query="test",
        note_types=["Task Item", "TaskItem", "task-item"],
    )

    assert captured_payload["note_types"] == ["task_item", "task_item", "task_item"]
