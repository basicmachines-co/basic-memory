"""Telemetry coverage for API v2 hydration utilities."""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from basic_memory.repository.search_index_row import SearchIndexRow

utils_module = importlib.import_module("basic_memory.api.v2.utils")


def _capture_spans():
    spans: list[tuple[str, dict]] = []

    @contextmanager
    def fake_span(name: str, **attrs):
        spans.append((name, attrs))
        yield

    return spans, fake_span


@pytest.mark.asyncio
async def test_to_search_results_emits_hydration_spans(monkeypatch) -> None:
    spans, fake_span = _capture_spans()
    monkeypatch.setattr(utils_module.telemetry, "span", fake_span)

    class FakeEntityService:
        async def get_entities_by_id(self, ids):
            return [
                SimpleNamespace(id=1, permalink="notes/root"),
                SimpleNamespace(id=2, permalink="notes/child"),
            ]

    now = datetime.now(timezone.utc)
    results = [
        SearchIndexRow(
            project_id=1,
            id=1,
            type="relation",
            file_path="notes/root.md",
            created_at=now,
            updated_at=now,
            permalink="notes/root/relates_to/notes/child",
            entity_id=1,
            from_id=1,
            to_id=2,
            relation_type="relates_to",
            title="Root relates to Child",
            score=1.0,
        )
    ]

    search_results = await utils_module.to_search_results(FakeEntityService(), results)

    assert search_results[0].relation_type == "relates_to"
    assert [name for name, _ in spans] == [
        "search.hydrate_results",
        "search.hydrate_results.fetch_entities",
        "search.hydrate_results.shape_results",
    ]
