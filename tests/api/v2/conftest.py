"""Fixtures for V2 API tests."""

from typing import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from basic_memory.deps import get_engine_factory, get_app_config
from basic_memory.models import Project


@pytest_asyncio.fixture
async def app(test_config, engine_factory, app_config) -> FastAPI:
    """Create FastAPI test application."""
    from basic_memory.api.app import app

    app.dependency_overrides[get_app_config] = lambda: app_config
    app.dependency_overrides[get_engine_factory] = lambda: engine_factory
    return app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Create client using ASGI transport - same as CLI will use."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


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
