"""Telemetry coverage for the v2 search router."""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from typing import Any, cast

import pytest

from basic_memory.schemas.search import SearchQuery

search_router_module = importlib.import_module("basic_memory.api.v2.routers.search_router")


@pytest.mark.asyncio
async def test_search_router_wraps_request_in_manual_operation() -> None:
    router = cast(Any, search_router_module)
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

    original_operation = router.telemetry.operation
    original_to_search_results = router.to_search_results
    router.telemetry.operation = fake_operation
    router.to_search_results = fake_to_search_results
    try:
        response = await router.search(
            SearchQuery(text="hello world"),
            FakeSearchService(),
            object(),
            project_id=123,
            page=2,
            page_size=5,
        )
    finally:
        router.telemetry.operation = original_operation
        router.to_search_results = original_to_search_results

    assert response.current_page == 2
    assert operations == [
        (
            "api.request.search",
            {
                "entrypoint": "api",
                "domain": "search",
                "action": "search",
                "page": 2,
                "page_size": 5,
                "retrieval_mode": "fts",
                "has_query": True,
                "has_filters": False,
            },
        )
    ]
