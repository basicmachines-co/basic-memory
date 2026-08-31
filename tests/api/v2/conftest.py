"""Fixtures for V2 API tests."""

from collections.abc import Generator
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from basic_memory.deps import get_app_config, get_engine_factory
from basic_memory.deps.services import (
    get_entity_vector_sync_scheduler,
    get_relation_resolution_scheduler,
)
from basic_memory.models import Project


@pytest_asyncio.fixture
async def app(test_config, engine_factory, app_config) -> AsyncGenerator[FastAPI, None]:
    """Create FastAPI test application."""
    from basic_memory.api.app import app

    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_app_config] = lambda: app_config
    app.dependency_overrides[get_engine_factory] = lambda: engine_factory
    try:
        yield app
    finally:
        # Trigger: the FastAPI app is a module-level singleton shared across tests.
        # Why: dependency overrides that capture a per-test engine can leak into
        # later CLI/MCP tests and create connections outside fixture ownership.
        # Outcome: each API test leaves the shared app exactly as it found it.
        app.dependency_overrides = previous_overrides


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Create client using ASGI transport - same as CLI will use."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture(autouse=True)
def vector_sync_scheduler_spy(app: FastAPI) -> Generator[list[dict[str, Any]], None, None]:
    """Capture scheduled vector sync work without executing it."""
    scheduled: list[dict[str, Any]] = []

    class VectorSyncSchedulerSpy:
        def schedule_entity_vector_sync(self, *, entity_id: int, project_id: int) -> None:
            scheduled.append({"entity_id": entity_id, "project_id": project_id})

    app.dependency_overrides[get_entity_vector_sync_scheduler] = lambda: VectorSyncSchedulerSpy()
    yield scheduled
    app.dependency_overrides.pop(get_entity_vector_sync_scheduler, None)


@pytest.fixture(autouse=True)
def relation_resolution_scheduler_spy(app: FastAPI) -> Generator[list[dict[str, Any]], None, None]:
    """Capture scheduled forward-reference resolution without executing it."""
    scheduled: list[dict[str, Any]] = []

    class RelationResolutionSchedulerSpy:
        def schedule_relation_resolution(self, *, project_id: int) -> None:
            scheduled.append({"project_id": project_id})

    app.dependency_overrides[get_relation_resolution_scheduler] = lambda: (
        RelationResolutionSchedulerSpy()
    )
    yield scheduled
    app.dependency_overrides.pop(get_relation_resolution_scheduler, None)


@pytest.fixture
def v2_project_url(test_project: Project) -> str:
    """Create a URL prefix for v2 project-scoped routes using project external_id.

    This helps tests generate the correct URL for v2 project-scoped routes
    which use external_id UUIDs instead of permalinks or integer IDs.
    """
    return f"/v2/projects/{test_project.external_id}"


@pytest.fixture
def v2_projects_url() -> str:
    """Base URL for v2 project management endpoints."""
    return "/v2/projects"


@pytest.fixture
def fake_read_cache(app: FastAPI):
    """Install an in-memory ReadCache backend so cached-read branches execute.

    The real backend is Redis and absent in tests (get_read_cache returns None,
    disabling read-through). Tests that pin cache semantics — a cached full note
    sliced per request, a cached resource sliced by Range — opt in with this
    fixture and can inspect the stored payloads.
    """
    from basic_memory.deps import get_read_cache
    from basic_memory.read_cache import (
        ReadCacheInvalidationStatus,
        ReadCacheKey,
        ReadCacheLookup,
        ReadCacheStoreStatus,
    )

    class InMemoryReadCache:
        def __init__(self) -> None:
            self.payloads: dict[ReadCacheKey, bytes] = {}
            self.invalidated_projects: list[str] = []

        async def lookup(self, key: ReadCacheKey) -> ReadCacheLookup:
            return ReadCacheLookup(generation="test-generation", payload=self.payloads.get(key))

        async def store(
            self,
            key: ReadCacheKey,
            lookup: ReadCacheLookup,
            payload: bytes,
            *,
            ttl_seconds: int,
        ) -> ReadCacheStoreStatus:
            del lookup, ttl_seconds
            self.payloads[key] = payload
            return ReadCacheStoreStatus.stored

        async def invalidate_project(self, project_id: str) -> ReadCacheInvalidationStatus:
            self.invalidated_projects.append(project_id)
            self.payloads.clear()
            return ReadCacheInvalidationStatus.invalidated

    cache = InMemoryReadCache()
    app.dependency_overrides[get_read_cache] = lambda: cache
    yield cache
    app.dependency_overrides.pop(get_read_cache, None)
