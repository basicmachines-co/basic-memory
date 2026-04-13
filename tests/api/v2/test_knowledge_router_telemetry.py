"""Telemetry coverage for the v2 knowledge router."""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast

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


@pytest.mark.asyncio
async def test_create_entity_emits_only_root_span(monkeypatch) -> None:
    spans, fake_span = _capture_spans()
    monkeypatch.setattr(knowledge_router_module.telemetry, "span", fake_span)

    entity = _fake_entity()
    response_content = (
        "---\ntitle: Telemetry Entity\ntype: note\npermalink: notes/test\n---\n\ntelemetry content"
    )

    class FakeEntityService:
        async def create_entity_with_content(self, data):
            return SimpleNamespace(
                entity=entity,
                content=response_content,
                search_content="telemetry content",
            )

    class FakeSearchService:
        async def index_entity(self, entity, content=None):
            assert content == "telemetry content"
            return None

    class FakeTaskScheduler:
        def schedule(self, *args, **kwargs):
            return None

    result = await knowledge_router_module.create_entity(
        project_id=123,
        data=Entity(
            title="Telemetry Entity",
            directory="notes",
            note_type="note",
            content_type="text/markdown",
            content="telemetry content",
        ),
        entity_service=cast(Any, FakeEntityService()),
        search_service=cast(Any, FakeSearchService()),
        task_scheduler=FakeTaskScheduler(),
        app_config=cast(Any, SimpleNamespace(semantic_search_enabled=False)),
    )

    assert result.content == response_content
    _assert_only_root_span(spans, "api.request.knowledge.create_entity")


@pytest.mark.asyncio
async def test_update_entity_emits_only_root_span(monkeypatch) -> None:
    spans, fake_span = _capture_spans()
    monkeypatch.setattr(knowledge_router_module.telemetry, "span", fake_span)

    entity = _fake_entity()
    response_content = "---\ntitle: Telemetry Entity\ntype: note\npermalink: notes/test\n---\n\nupdated telemetry content"

    class FakeEntityService:
        async def update_entity_with_content(self, existing, data):
            return SimpleNamespace(
                entity=entity,
                content=response_content,
                search_content="updated telemetry content",
            )

    class FakeSearchService:
        async def index_entity(self, entity, content=None):
            assert content == "updated telemetry content"
            return None

    class FakeEntityRepository:
        async def get_by_external_id(self, external_id):
            return entity

    class FakeTaskScheduler:
        def schedule(self, *args, **kwargs):
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
        entity_service=cast(Any, FakeEntityService()),
        search_service=cast(Any, FakeSearchService()),
        entity_repository=cast(Any, FakeEntityRepository()),
        task_scheduler=FakeTaskScheduler(),
        app_config=cast(Any, SimpleNamespace(semantic_search_enabled=False)),
        entity_id=entity.external_id,
    )

    assert result.content == response_content
    _assert_only_root_span(spans, "api.request.knowledge.update_entity")


@pytest.mark.asyncio
async def test_edit_entity_emits_only_root_span(monkeypatch) -> None:
    spans, fake_span = _capture_spans()
    monkeypatch.setattr(knowledge_router_module.telemetry, "span", fake_span)

    entity = _fake_entity()
    response_content = "---\ntitle: Telemetry Entity\ntype: note\npermalink: notes/test\n---\n\nedited telemetry content"

    class FakeEntityService:
        async def edit_entity_with_content(self, **kwargs):
            return SimpleNamespace(
                entity=entity,
                content=response_content,
                search_content="edited telemetry content",
            )

    class FakeSearchService:
        async def index_entity(self, entity, content=None):
            assert content == "edited telemetry content"
            return None

    class FakeEntityRepository:
        async def get_by_external_id(self, external_id):
            return entity

    class FakeTaskScheduler:
        def schedule(self, *args, **kwargs):
            return None

    result = await knowledge_router_module.edit_entity_by_id(
        data=EditEntityRequest(operation="append", content="edited telemetry content"),
        project_id=123,
        entity_service=cast(Any, FakeEntityService()),
        search_service=cast(Any, FakeSearchService()),
        entity_repository=cast(Any, FakeEntityRepository()),
        task_scheduler=FakeTaskScheduler(),
        app_config=cast(Any, SimpleNamespace(semantic_search_enabled=False)),
        entity_id=entity.external_id,
    )

    assert result.content == response_content
    _assert_only_root_span(spans, "api.request.knowledge.edit_entity")
