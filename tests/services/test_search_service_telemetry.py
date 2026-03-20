"""Telemetry coverage for search execution phases."""

from __future__ import annotations

import importlib
from contextlib import contextmanager

import pytest

from basic_memory.schemas.search import SearchQuery

search_service_module = importlib.import_module("basic_memory.services.search_service")


def _capture_spans():
    spans: list[tuple[str, dict]] = []

    @contextmanager
    def fake_span(name: str, **attrs):
        spans.append((name, attrs))
        yield

    return spans, fake_span


@pytest.mark.asyncio
async def test_search_service_wraps_repository_search(search_service, monkeypatch) -> None:
    spans, fake_span = _capture_spans()
    monkeypatch.setattr(search_service_module.telemetry, "span", fake_span)

    await search_service.search(SearchQuery(text="Root Entity"))

    assert spans[0] == (
        "search.execute",
        {
            "retrieval_mode": "fts",
            "has_text_query": True,
            "has_title_query": False,
            "has_permalink_query": False,
            "has_metadata_filters": False,
            "limit": 10,
            "offset": 0,
        },
    )


@pytest.mark.asyncio
async def test_search_service_emits_relaxed_retry_span(search_service, monkeypatch) -> None:
    spans, fake_span = _capture_spans()
    calls: list[dict] = []

    async def fake_repository_search(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(search_service_module.telemetry, "span", fake_span)
    monkeypatch.setattr(search_service.repository, "search", fake_repository_search)

    await search_service.search(SearchQuery(text="who are our main competitors and partners"))

    assert [name for name, _ in spans] == ["search.execute", "search.relaxed_fts_retry"]
    assert spans[1] == (
        "search.relaxed_fts_retry",
        {
            "retrieval_mode": "fts",
            "token_count": 7,
            "limit": 10,
            "offset": 0,
        },
    )
    assert len(calls) == 2
    assert calls[0]["search_text"] == "who are our main competitors and partners"
    assert calls[1]["search_text"] == "main OR competitors OR partners"
