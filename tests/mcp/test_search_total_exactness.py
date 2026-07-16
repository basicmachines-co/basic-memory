"""Tests for the structured search total exactness contract."""

from contextlib import asynccontextmanager
import importlib
from types import SimpleNamespace

import pytest

from basic_memory.schemas.search import SearchItemType, SearchResponse, SearchResult


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("search_type", "retrieval_mode", "total", "total_is_exact"),
    [
        ("text", "fts", 1, True),
        ("vector", "vector", 0, False),
        ("hybrid", "hybrid", 0, False),
    ],
)
async def test_search_notes_json_exposes_total_exactness(
    monkeypatch,
    search_type: str,
    retrieval_mode: str,
    total: int,
    total_is_exact: bool,
) -> None:
    """MCP JSON preserves the API's exact-versus-unknown total distinction."""
    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")
    captured_payload: dict[str, object] = {}

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield object(), SimpleNamespace(name="test-project", external_id="test-project-id")

    async def fake_resolve_project_and_path(client, identifier, project=None, context=None):
        return None, identifier, False

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, *, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(
                results=[
                    SearchResult(
                        title="Result",
                        type=SearchItemType.ENTITY,
                        score=1.0,
                        permalink="notes/result",
                        file_path="notes/result.md",
                    )
                ],
                current_page=page,
                page_size=page_size,
                total=total,
                total_is_exact=total_is_exact,
            )

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    result = await search_mod.search_notes(
        project="test-project",
        query="query",
        search_type=search_type,
        output_format="json",
    )

    assert isinstance(result, dict)
    assert captured_payload["retrieval_mode"] == retrieval_mode
    assert result["total"] == total
    assert result["total_is_exact"] is total_is_exact
