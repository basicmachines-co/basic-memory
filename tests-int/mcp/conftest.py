"""Tests for the MCP server implementation using FastAPI TestClient."""

from typing import AsyncGenerator

import pytest
import pytest_asyncio
from basic_memory.config import ProjectConfig, BasicMemoryConfig
from basic_memory.models import Project
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from mcp.server import FastMCP

from basic_memory.api.app import app as fastapi_app
from basic_memory.deps import get_project_config, get_engine_factory, get_app_config
from basic_memory.services.search_service import SearchService
from basic_memory.mcp.server import mcp as mcp_server

from basic_memory.config import app_config as basic_memory_app_config  # noqa: F401


@pytest.fixture(scope="function")
def mcp() -> FastMCP:
    return mcp_server

@pytest_asyncio.fixture(scope="function")
async def second_project(app_config, project_repository, tmp_path) -> Project:
    """Create a second project config for testing."""
    second_project_data = {
        "name": "read-test-project",
        "description": "Project for read testing",
        "path": f"{tmp_path}/read-test-project",
        "is_active": True,
        "is_default": False,
    }
    second_project = await project_repository.create(second_project_data)
    app_config.projects[second_project.name] = str(second_project.path)
    return second_project


@pytest.fixture(scope="function")
def app(app_config, project_config, engine_factory, project_session) -> FastAPI:
    """Create test FastAPI application."""
    app = fastapi_app
    app.dependency_overrides[get_project_config] = lambda: project_config
    app.dependency_overrides[get_engine_factory] = lambda: engine_factory
    return app


@pytest.fixture(scope="function")
def multiple_app_config(test_project, second_project, ) -> BasicMemoryConfig:
    projects = {test_project.name: str(test_project.path),
                second_project.name: str(second_project.path)}
    app_config = BasicMemoryConfig(env="test", projects=projects, default_project=test_project.name)

    # set the module app_config instance project list
    basic_memory_app_config.projects = projects
    basic_memory_app_config.default_project = test_project.name

    return app_config


@pytest.fixture(scope="function")
def multi_project_app(multiple_app_config, engine_factory, project_session) -> FastAPI:
    """Create test FastAPI application. """

    # override the app config with two projects
    app = fastapi_app
    app.dependency_overrides[get_app_config] = lambda: multiple_app_config
    app.dependency_overrides[get_engine_factory] = lambda: engine_factory
    return app


@pytest_asyncio.fixture(scope="function")
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Create test client that both MCP and tests will use."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
def test_entity_data():
    """Sample data for creating a test entity."""
    return {
        "entities": [
            {
                "title": "Test Entity",
                "entity_type": "test",
                "summary": "",  # Empty string instead of None
            }
        ]
    }


@pytest_asyncio.fixture(autouse=True)
async def init_search_index(search_service: SearchService):
    await search_service.init_search_index()