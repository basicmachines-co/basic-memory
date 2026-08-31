"""Tests for the MCP server implementation using FastAPI TestClient."""

from collections.abc import AsyncGenerator, Generator
from typing import Any, cast

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastmcp import FastMCP
from httpx import AsyncClient, ASGITransport

from basic_memory import db
from basic_memory.api.app import app as fastapi_app
from basic_memory.config_models import ProjectEntry
from basic_memory.deps import get_engine_factory, get_app_config
from basic_memory.models.project import Project
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.services.search_service import SearchService
from basic_memory.mcp.server import mcp as mcp_server


@pytest.fixture(scope="function")
def mcp() -> FastMCP:
    return cast(Any, mcp_server)


class ContextState:
    """Minimal FastMCP context-state stub for MCP tests."""

    def __init__(self):
        self._state: dict[str, object] = {}
        self.request_context = None

    async def get_state(self, key: str):
        return self._state.get(key)

    async def set_state(self, key: str, value: object, **kwargs) -> None:
        self._state[key] = value

    async def info(self, message: str) -> None:
        self._state["info_message"] = message


def ctx(context: ContextState) -> Any:
    return cast(Any, context)


@pytest.fixture
def context_state() -> ContextState:
    return ContextState()


@pytest.fixture(scope="function")
def app(
    app_config, project_config, engine_factory, config_manager
) -> Generator[FastAPI, None, None]:
    """Create test FastAPI application."""
    app = fastapi_app
    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_app_config] = lambda: app_config
    app.dependency_overrides[get_engine_factory] = lambda: engine_factory
    try:
        yield app
    finally:
        # Trigger: the FastAPI app is a module-level singleton shared across tests.
        # Why: stale dependency overrides can hold onto a disposed per-test engine
        # and reopen SQLite connections during later unrelated tests.
        # Outcome: restore the shared app's override table after each MCP test.
        app.dependency_overrides = previous_overrides


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
                "note_type": "test",
                "summary": "",  # Empty string instead of None
            }
        ]
    }


@pytest_asyncio.fixture
async def init_search_index(search_service: SearchService):
    """Initialize search index. Request this fixture explicitly in tests that need it."""
    await search_service.init_search_index()


@pytest_asyncio.fixture
async def second_project(config_manager, engine_factory, tmp_path_factory) -> Project:
    """A second active project (DB row + config entry) for multi-project routing tests.

    Both halves matter: the API resolves projects from the database, while the
    MCP client layer routes by the local config — a project absent from config
    routes CLOUD by default and dies without credentials. The path lives outside
    config_home because test-project's path IS config_home; nesting one project
    inside another would make its files ambiguous between the two.
    """
    project_path = tmp_path_factory.mktemp("second-project-home")
    _, session_maker = engine_factory
    async with db.scoped_session(session_maker) as session:
        project = await ProjectRepository().create(
            session,
            {
                "name": "second-project",
                "description": "Second project for multi-project routing tests",
                "path": str(project_path),
                "is_active": True,
                "is_default": False,
            },
        )

    config = config_manager.load_config()
    config.projects["second-project"] = ProjectEntry(path=str(project_path))
    config_manager.save_config(config)
    return project
