"""Telemetry coverage for search execution phases."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from basic_memory.schemas.search import SearchQuery


def _capture_spans():
    spans: list[tuple[str, dict]] = []

    @contextmanager
    def fake_span(name: str, **attrs):
        spans.append((name, attrs))
        yield

    return spans, fake_span


@pytest.mark.asyncio
async def test_search_service_wraps_repository_search(search_service, monkeypatch) -> None:
    import logfire

    spans, fake_span = _capture_spans()
    monkeypatch.setattr(logfire, "span", fake_span)

    await search_service.search(SearchQuery(text="Root Entity"))

    assert spans == [
        (
            "search.execute",
            {
                "retrieval_mode": "fts",
                "has_query": True,
                "has_filters": False,
                "limit": 10,
                "offset": 0,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_search_service_delegates_relaxed_retry(search_service, monkeypatch) -> None:
    import logfire

    spans, fake_span = _capture_spans()
    calls: list[dict] = []

    async def fake_repository_search(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(logfire, "span", fake_span)
    monkeypatch.setattr(search_service.repository, "search", fake_repository_search)

    await search_service.search(SearchQuery(text="who are our main competitors and partners"))

    assert [name for name, _ in spans] == ["search.execute"]
    assert len(calls) == 1
    assert calls[0]["search_text"] == "who are our main competitors and partners"
    assert calls[0]["allow_relaxed"] is True
