"""Tests for knowledge graph API routes."""

from typing import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from basic_memory.deps import get_project_config, get_engine_factory, get_app_config
from basic_memory.models import Project


@pytest_asyncio.fixture
async def app(test_config, engine_factory, app_config) -> FastAPI:
    """Create FastAPI test application."""
    from basic_memory.api.app import app

    app.dependency_overrides[get_app_config] = lambda: app_config
    app.dependency_overrides[get_project_config] = lambda: test_config.project_config
    app.dependency_overrides[get_engine_factory] = lambda: engine_factory
    return app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Create client using ASGI transport - same as CLI will use."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
def project_url(test_project: Project) -> str:
    """Create a URL prefix for the project routes.

    This helps tests generate the correct URL for project-scoped routes.
    """
    # Make sure this matches what's in tests/conftest.py for test_project creation
    # The permalink should be generated from "Test Project Context"
    return f"/{test_project.permalink}"