"""Telemetry coverage for the v2 search router."""

from __future__ import annotations

import importlib
from contextlib import contextmanager

import pytest

from basic_memory.schemas.search import SearchQuery

search_router_module = importlib.import_module("basic_memory.api.v2.routers.search_router")


@pytest.mark.asyncio
async def test_search_router_wraps_request_in_manual_operation() -> None:
    operations: list[tuple[str, dict]] = []

    class FakeSearchService:
        async def search(self, query, *, limit, offset):
            return []

    @contextmanager
    def fake_operation(name: str, **attrs):
        operations.append((name, attrs))
        yield

    async def fake_to_search_results(entity_service, results):
        return []

    original_operation = search_router_module.telemetry.operation
    original_to_search_results = search_router_module.to_search_results
    search_router_module.telemetry.operation = fake_operation
    search_router_module.to_search_results = fake_to_search_results
    try:
        response = await search_router_module.search(
            SearchQuery(text="hello world"),
            FakeSearchService(),
            object(),
            project_id="project-123",
            page=2,
            page_size=5,
        )
    finally:
        search_router_module.telemetry.operation = original_operation
        search_router_module.to_search_results = original_to_search_results

    assert response.current_page == 2
    assert operations == [
        (
            "api.request.search",
            {
                "entrypoint": "api",
                "page": 2,
                "page_size": 5,
                "retrieval_mode": "fts",
                "has_text_query": True,
                "has_title_query": False,
                "has_permalink_query": False,
            },
        )
    ]
