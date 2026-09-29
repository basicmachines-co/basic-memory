"""Account search carries query guidance only for a complete, empty first page."""

import importlib
from contextlib import asynccontextmanager
from typing import Literal
from unittest.mock import AsyncMock

import pytest

from basic_memory.mcp.tools.search import SearchProjectRef
from basic_memory.schemas.search import SearchItemType, SearchResponse, SearchResult

ONE = SearchProjectRef(
    name="one",
    external_id="11111111-1111-1111-1111-111111111111",
    id=1,
    workspace_tenant_id=None,
    path="/one",
)
TWO = SearchProjectRef(
    name="two",
    external_id="22222222-2222-2222-2222-222222222222",
    id=2,
    workspace_tenant_id="tenant-two",
    path="/two",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["empty", "hit", "failed", "later"])
@pytest.mark.parametrize("output_format", ["text", "json"])
async def test_fanout_query_hint(monkeypatch, case: str, output_format: Literal["text", "json"]):
    search = importlib.import_module("basic_memory.mcp.tools.search")
    clients = importlib.import_module("basic_memory.mcp.clients")
    monkeypatch.setattr(search, "_load_search_project_refs", AsyncMock(return_value=[ONE, TWO]))
    monkeypatch.setattr(search, "project_index_required", AsyncMock(return_value=None))

    @asynccontextmanager
    async def fake_get_client(project_name=None, workspace=None):
        yield {"workspace": workspace}

    def empty(page: int, page_size: int) -> SearchResponse:
        return SearchResponse(
            results=[],
            current_page=page,
            page_size=page_size,
            total=0,
            total_is_exact=True,
            query_hint="Try a shorter word from your query.",
        )

    class StubScopedSearchClient:
        def __init__(self, client):
            self.workspace = client["workspace"]

        async def search(self, payload, *, project_ids, page, page_size):
            # The first database (local) is always empty; the second database varies.
            if self.workspace is None:
                return empty(page, page_size)
            if case == "hit":
                return SearchResponse(
                    results=[
                        SearchResult(
                            title="Match",
                            type=SearchItemType.ENTITY,
                            score=1.0,
                            file_path="note.md",
                            permalink="note",
                            project_id=TWO.id,
                            project_external_id=TWO.external_id,
                        )
                    ],
                    current_page=page,
                    page_size=page_size,
                    total=1,
                    total_is_exact=True,
                )
            if case == "failed":
                raise RuntimeError("access denied")
            return empty(page, page_size)

    monkeypatch.setattr(search, "get_client", fake_get_client)
    monkeypatch.setattr(clients, "ScopedSearchClient", StubScopedSearchClient)

    result = await search._search_all_projects(
        query="雾凇拼音",
        page=2 if case == "later" else 1,
        page_size=10,
        search_type="text",
        output_format=output_format,
        note_types=[],
        entity_types=[],
        categories=[],
        after_date=None,
        metadata_filters=None,
        tags=None,
        status=None,
        min_similarity=None,
        valid_at=None,
        valid_overlaps=None,
        time_kind=None,
        context=None,
    )
    if isinstance(result, str):
        assert ("shorter word" in result) == (case == "empty")
    else:
        assert (result.get("query_hint") is not None) == (case == "empty")
