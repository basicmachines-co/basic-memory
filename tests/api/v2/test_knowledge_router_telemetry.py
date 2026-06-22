"""Telemetry coverage for the v2 knowledge router."""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast

import logfire
import pytest
from fastapi import Response

from basic_memory.schemas.base import Entity
from basic_memory.schemas.request import EditEntityRequest

knowledge_router_module = importlib.import_module("basic_memory.api.v2.routers.knowledge_router")


def _capture_spans():
    spans: list[tuple[str, dict]] = []

    @contextmanager
    def fake_span(name: str, **attrs):
        spans.append((name, attrs))
        yield

    return spans, fake_span


def _fake_entity(*, external_id: str = "entity-123", file_path: str = "notes/test.md"):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        external_id=external_id,
        id=1,
        title="Telemetry Entity",
        note_type="note",
        content_type="text/markdown",
        permalink="notes/test",
        file_path=file_path,
        entity_metadata=None,
        observations=[],
        relations=[],
        created_at=now,
        updated_at=now,
        created_by=None,
        last_updated_by=None,
    )


def _assert_only_root_span(spans: list[tuple[str, dict]], expected_name: str) -> None:
    assert [name for name, _ in spans] == [expected_name]


def _accepted_change(entity, response_content: str, *, status_code: int = 200):
    return SimpleNamespace(
        status_code=status_code,
        payload={
            "external_id": entity.external_id,
            "id": entity.id,
            "title": entity.title,
            "note_type": entity.note_type,
            "content_type": entity.content_type,
            "permalink": entity.permalink,
            "file_path": entity.file_path,
            "content": response_content,
            "entity_metadata": entity.entity_metadata,
            "observations": [],
            "relations": [],
            "created_at": entity.created_at.isoformat(),
            "updated_at": entity.updated_at.isoformat(),
            "created_by": entity.created_by,
            "last_updated_by": entity.last_updated_by,
            "api_version": "v2",
        },
        materialization=object(),
        file_delete=None,
    )


@pytest.mark.asyncio
async def test_create_entity_emits_only_root_span(monkeypatch) -> None:
    spans, fake_span = _capture_spans()
    monkeypatch.setattr(logfire, "span", fake_span)

    entity = _fake_entity()
    response_content = (
        "---\ntitle: Telemetry Entity\ntype: note\npermalink: notes/test\n---\n\ntelemetry content"
    )

    class FakeNoteContentMutationService:
        async def create_note(self, **kwargs):
            return _accepted_change(entity, response_content, status_code=201)

    class FakeNoteContentMaterializationProvider:
        async def materialize_write_change(self, accepted):
            assert accepted.materialization is not None

    class FakeVectorSyncScheduler:
        def schedule_entity_vector_sync(self, *args, **kwargs):
            return None

    result = await knowledge_router_module.create_entity(
        project_id=123,
        project_external_id="project-123",
        data=Entity(
            title="Telemetry Entity",
            directory="notes",
            note_type="note",
            content_type="text/markdown",
            content="telemetry content",
        ),
        note_content_mutation_service=cast(Any, FakeNoteContentMutationService()),
        note_content_materialization_provider=cast(Any, FakeNoteContentMaterializationProvider()),
        vector_sync_scheduler=FakeVectorSyncScheduler(),
        app_config=cast(Any, SimpleNamespace(semantic_search_enabled=False)),
    )

    assert result.content == response_content
    _assert_only_root_span(spans, "api.request.knowledge.create_entity")


@pytest.mark.asyncio
async def test_update_entity_emits_only_root_span(monkeypatch) -> None:
    spans, fake_span = _capture_spans()
    monkeypatch.setattr(logfire, "span", fake_span)

    entity = _fake_entity()
    response_content = "---\ntitle: Telemetry Entity\ntype: note\npermalink: notes/test\n---\n\nupdated telemetry content"

    class FakeNoteContentMutationService:
        async def update_note(self, **kwargs):
            return _accepted_change(entity, response_content)

    class FakeNoteContentMaterializationProvider:
        async def materialize_write_change(self, accepted):
            assert accepted.materialization is not None

    class FakeVectorSyncScheduler:
        def schedule_entity_vector_sync(self, *args, **kwargs):
            return None

    response = Response()
    result = await knowledge_router_module.update_entity_by_id(
        data=Entity(
            title="Telemetry Entity",
            directory="notes",
            note_type="note",
            content_type="text/markdown",
            content="updated telemetry content",
        ),
        response=response,
        project_id=123,
        project_external_id="project-123",
        note_content_mutation_service=cast(Any, FakeNoteContentMutationService()),
        note_content_materialization_provider=cast(Any, FakeNoteContentMaterializationProvider()),
        vector_sync_scheduler=FakeVectorSyncScheduler(),
        app_config=cast(Any, SimpleNamespace(semantic_search_enabled=False)),
        entity_id=entity.external_id,
    )

    assert result.content == response_content
    _assert_only_root_span(spans, "api.request.knowledge.update_entity")


@pytest.mark.asyncio
async def test_edit_entity_emits_only_root_span(monkeypatch) -> None:
    spans, fake_span = _capture_spans()
    monkeypatch.setattr(logfire, "span", fake_span)

    entity = _fake_entity()
    response_content = "---\ntitle: Telemetry Entity\ntype: note\npermalink: notes/test\n---\n\nedited telemetry content"

    class FakeNoteContentMutationService:
        async def edit_note(self, **kwargs):
            return _accepted_change(entity, response_content)

    class FakeNoteContentMaterializationProvider:
        async def materialize_write_change(self, accepted):
            assert accepted.materialization is not None

    class FakeVectorSyncScheduler:
        def schedule_entity_vector_sync(self, *args, **kwargs):
            return None

    result = await knowledge_router_module.edit_entity_by_id(
        data=EditEntityRequest(operation="append", content="edited telemetry content"),
        project_id=123,
        project_external_id="project-123",
        note_content_mutation_service=cast(Any, FakeNoteContentMutationService()),
        note_content_materialization_provider=cast(Any, FakeNoteContentMaterializationProvider()),
        vector_sync_scheduler=FakeVectorSyncScheduler(),
        app_config=cast(Any, SimpleNamespace(semantic_search_enabled=False)),
        entity_id=entity.external_id,
    )

    assert result.content == response_content
    _assert_only_root_span(spans, "api.request.knowledge.edit_entity")
