"""Telemetry coverage for the v2 search router."""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from typing import Any, cast

import logfire
import pytest

from basic_memory.schemas.search import SearchQuery

search_router_module = importlib.import_module("basic_memory.api.v2.routers.search_router")


@pytest.mark.asyncio
async def test_search_router_wraps_request_in_manual_operation(monkeypatch) -> None:
    router = cast(Any, search_router_module)
    operations: list[tuple[str, dict]] = []

    class FakeSearchService:
        async def search(self, query, *, limit, offset):
            return []

    @contextmanager
    def fake_span(name: str, **attrs):
        operations.append((name, attrs))
        yield

    async def fake_to_search_results(entity_service, results):
        return []

    monkeypatch.setattr(logfire, "span", fake_span)
    monkeypatch.setattr(router, "to_search_results", fake_to_search_results)

    response = await router.search(
        SearchQuery(text="hello world"),
        FakeSearchService(),
        object(),
        project_id=123,
        page=2,
        page_size=5,
    )

    assert response.current_page == 2
    # The root span fires with these attrs; additional nested spans may fire as well.
    assert operations[0] == (
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
