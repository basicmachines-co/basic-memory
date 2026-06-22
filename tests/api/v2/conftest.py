"""Fixtures for V2 API tests."""

from collections.abc import Generator
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from basic_memory.deps import get_app_config, get_engine_factory
from basic_memory.deps.services import get_entity_vector_sync_scheduler
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
