"""Accepted note writes time each step of the accept path in its own span.

A slow note save is diagnosed from these spans: each names one step (actor
resolution, freshening the stored file, the read-cache scope, the accept
transaction, its commit, the post-commit work and the read-cache invalidation),
so a trace shows where the
write spent its time instead of one unexplained block.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from logfire.testing import CaptureLogfire, capfire as capfire

from basic_memory.deps.read_cache import get_read_cache
from basic_memory.read_cache import ReadCacheChange, ReadCacheInvalidationStatus


class RecordingReadCache:
    """Stand in for a host's read cache, which the local API does not have."""

    def __init__(self) -> None:
        self.invalidations = 0

    async def invalidate(
        self, project_id: str, change: ReadCacheChange
    ) -> ReadCacheInvalidationStatus:
        self.invalidations += 1
        return ReadCacheInvalidationStatus.invalidated

    async def invalidate_project(self, project_id: str) -> ReadCacheInvalidationStatus:
        self.invalidations += 1
        return ReadCacheInvalidationStatus.invalidated


@pytest.fixture(autouse=True)
def read_cache(app: FastAPI) -> RecordingReadCache:
    cache = RecordingReadCache()

    async def host_read_cache() -> RecordingReadCache:
        return cache

    app.dependency_overrides[get_read_cache] = host_read_cache
    return cache


CACHE_SCOPE_SPAN = "note write scopes read cache changes"
# Steps every create, update and edit runs, whatever the runtime.
SHARED_STEP_SPANS = {
    CACHE_SCOPE_SPAN,
    "note write runs accept transaction",
    "note write applies accepted {mutation_kind}",
    "note write runs accepted mutation hook",
    "note write commits accepted transaction",
    "note write finishes accepted mutation",
    "note write refreshes search index",
    "note write publishes relations",
    "note write reads linked notes",
    "note write invalidates read cache",
}
# The local API freshens an existing note from disk before changing it.
FRESHEN_SPAN = "note write freshens existing note content"
ROOT_SPAN = "note write accepts {mutation_kind}"


def _spans(capfire: CaptureLogfire) -> list[dict[str, Any]]:
    return capfire.exporter.exported_spans_as_dict()


def _assert_accept_spans(
    spans: list[dict[str, Any]], *, mutation_kind: str, freshens: bool
) -> None:
    names = {span["name"] for span in spans}
    expected = SHARED_STEP_SPANS | {ROOT_SPAN}
    if freshens:
        expected |= {FRESHEN_SPAN}
    assert expected <= names, expected - names

    root = next(span for span in spans if span["name"] == ROOT_SPAN)
    assert root["attributes"]["mutation_kind"] == mutation_kind
    applied = next(
        span for span in spans if span["name"] == "note write applies accepted {mutation_kind}"
    )
    assert applied["attributes"]["mutation_kind"] == mutation_kind

    # Every step span sits inside the root span, so one trace query groups a write.
    root_trace = root["context"]["trace_id"]
    for span in spans:
        if span["name"] in expected:
            assert span["context"]["trace_id"] == root_trace, span["name"]
            assert span["start_time"] >= root["start_time"], span["name"]
            assert span["end_time"] <= root["end_time"], span["name"]

    # The read-cache scope span covers the scope's whole life: the transaction it
    # wraps and the invalidation it runs on exit.
    scope = next(span for span in spans if span["name"] == CACHE_SCOPE_SPAN)
    for inner_name in ("note write runs accept transaction", "note write invalidates read cache"):
        inner = next(span for span in spans if span["name"] == inner_name)
        assert inner["start_time"] >= scope["start_time"], inner_name
        assert inner["end_time"] <= scope["end_time"], inner_name


@pytest.mark.asyncio
async def test_create_update_and_edit_emit_a_span_per_accept_step(
    client: AsyncClient, v2_project_url: str, capfire: CaptureLogfire
) -> None:
    capfire.exporter.clear()
    created = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Timed", "directory": "notes", "content": "First"},
        params={"fast": False},
    )
    assert created.status_code == 202, created.text
    _assert_accept_spans(_spans(capfire), mutation_kind="create", freshens=False)
    external_id = created.json()["external_id"]

    capfire.exporter.clear()
    updated = await client.put(
        f"{v2_project_url}/knowledge/entities/{external_id}",
        json={"title": "Timed", "directory": "notes", "content": "Second"},
        params={"fast": False},
    )
    assert updated.status_code == 202, updated.text
    _assert_accept_spans(_spans(capfire), mutation_kind="update", freshens=True)

    capfire.exporter.clear()
    edited = await client.patch(
        f"{v2_project_url}/knowledge/entities/{external_id}",
        json={"operation": "append", "content": "Third"},
        params={"fast": False},
    )
    assert edited.status_code == 202, edited.text
    _assert_accept_spans(_spans(capfire), mutation_kind="edit", freshens=True)


@pytest.mark.asyncio
async def test_rejected_write_skips_the_commit_span(
    client: AsyncClient, v2_project_url: str, capfire: CaptureLogfire
) -> None:
    """A write refused inside the transaction rolls back and records no commit."""
    created = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "Guarded", "directory": "notes", "content": "First"},
        params={"fast": False},
    )
    assert created.status_code == 202, created.text
    external_id = created.json()["external_id"]

    capfire.exporter.clear()
    refused = await client.put(
        f"{v2_project_url}/knowledge/entities/{external_id}",
        json={"title": "Guarded", "directory": "notes", "content": "Second"},
        headers={"x-bm-cloud-note-base-checksum": "stale"},
        params={"fast": False},
    )
    assert refused.status_code == 409, refused.text
    names = {span["name"] for span in _spans(capfire)}
    assert "note write runs accept transaction" in names
    assert "note write commits accepted transaction" not in names

    current = await client.get(f"{v2_project_url}/knowledge/entities/{external_id}")
    assert current.status_code == 200, current.text
