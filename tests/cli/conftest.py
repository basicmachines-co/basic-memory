from typing import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from basic_memory.api.app import app as fastapi_app
from basic_memory.deps import get_project_config, get_engine_factory, get_app_config


@pytest_asyncio.fixture(autouse=True)
async def app(app_config, project_config, engine_factory, test_config) -> FastAPI:
    """Create test FastAPI application."""
    app = fastapi_app
    app.dependency_overrides[get_app_config] = lambda: app_config
    app.dependency_overrides[get_project_config] = lambda: project_config
    app.dependency_overrides[get_engine_factory] = lambda: engine_factory
    return app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Create test client that both MCP and tests will use."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
def cli_env(project_config, client, test_config):
    """Set up CLI environment with correct project session."""
    return {"project_config": project_config, "client": client}