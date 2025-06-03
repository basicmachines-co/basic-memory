"""
Shared fixtures for integration tests.

Integration tests verify the complete flow: MCP Client → MCP Server → FastAPI → Database.
Unlike unit tests which use in-memory databases and mocks, integration tests use real SQLite
files and test the full application stack to ensure all components work together correctly.

## Architecture

The integration test setup creates this flow:

```
Test → MCP Client → MCP Server → HTTP Request (ASGITransport) → FastAPI App → Database
                                                                      ↑
                                                               Dependency overrides
                                                               point to test database
```

## Key Components

1. **Real SQLite Database**: Uses `DatabaseType.FILESYSTEM` with actual SQLite files
   in temporary directories instead of in-memory databases.

2. **Shared Database Connection**: Both MCP server and FastAPI app use the same
   database via dependency injection overrides.

3. **Project Session Management**: Initializes the MCP project session with test
   project configuration so tools know which project to operate on.

4. **Search Index Initialization**: Creates the FTS5 search index tables that
   the application requires for search functionality.

5. **Global Configuration Override**: Modifies the global `basic_memory_app_config`
   so MCP tools use test project settings instead of user configuration.

## Usage

Integration tests should include both `mcp_server` and `app` fixtures to ensure
the complete stack is wired correctly:

```python
@pytest.mark.asyncio
async def test_my_mcp_tool(mcp_server, app):
    async with Client(mcp_server) as client:
        result = await client.call_tool("tool_name", {"param": "value"})
        # Assert on results...
```

The `app` fixture ensures FastAPI dependency overrides are active, and
`mcp_server` provides the MCP server with proper project session initialization.
"""
import os
from typing import AsyncGenerator
from unittest import mock
from unittest.mock import patch

import pytest
import pytest_asyncio
from pathlib import Path

from httpx import AsyncClient, ASGITransport

import basic_memory.config
import basic_memory.mcp.project_session

from basic_memory.config import BasicMemoryConfig, ProjectConfig, ConfigManager
from basic_memory.db import engine_session_factory, DatabaseType
from basic_memory.models import Project
from basic_memory.repository.project_repository import ProjectRepository
from fastapi import FastAPI

from basic_memory.api.app import app as fastapi_app
from basic_memory.deps import get_project_config, get_engine_factory, get_app_config


# Import MCP tools so they're available for testing
from basic_memory.mcp import tools  # noqa: F401


@pytest_asyncio.fixture(scope="function")
async def engine_factory(tmp_path):
    """Create a SQLite file engine factory for integration testing."""
    db_path = tmp_path / "test.db"
    async with engine_session_factory(db_path, DatabaseType.FILESYSTEM) as (
        engine,
        session_maker,
    ):
        # Initialize database schema
        from basic_memory.models.base import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        yield engine, session_maker


@pytest_asyncio.fixture(scope="function")
async def test_project(tmp_path, engine_factory) -> Project:
    """Create a test project."""
    project_data = {
        "name": "test-project",
        "description": "Project used for integration tests",
        "path": str(tmp_path),
        "is_active": True,
        "is_default": True,
    }

    engine, session_maker = engine_factory
    project_repository = ProjectRepository(session_maker)
    project = await project_repository.create(project_data)
    return project

@pytest.fixture
def config_home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path

@pytest.fixture(scope="function")
def app_config(config_home, test_project, tmp_path, monkeypatch) -> BasicMemoryConfig:
    """Create test app configuration."""
    projects = {test_project.name: str(test_project.path)}
    app_config = BasicMemoryConfig(env="test", projects=projects, default_project=test_project.name, update_permalinks_on_move=True)

    # Set the module app_config instance project list (like regular tests)
    monkeypatch.setattr("basic_memory.config.app_config", app_config)
    return app_config

@pytest.fixture
def config_manager(app_config: BasicMemoryConfig, config_home, monkeypatch) -> ConfigManager:
    config_manager = ConfigManager()
    # Update its paths to use the test directory
    config_manager.config_dir = config_home / ".basic-memory"
    config_manager.config_file = config_manager.config_dir / "config.json"
    config_manager.config_dir.mkdir(parents=True, exist_ok=True)

    # Override the config directly instead of relying on disk load
    config_manager.config = app_config

    # Ensure the config file is written to disk
    config_manager.save_config(app_config)

    # Patch the config_manager in all locations where it's imported
    monkeypatch.setattr("basic_memory.config.config_manager", config_manager)
    monkeypatch.setattr("basic_memory.services.project_service.config_manager", config_manager)

    return config_manager

@pytest.fixture
def project_session(test_project: Project):
    # initialize the project session with the test project
    basic_memory.mcp.project_session.session.initialize(test_project.name)


@pytest.fixture(scope="function")
def project_config(test_project, monkeypatch):
    """Create test project configuration."""

    project_config = ProjectConfig(
        name=test_project.name,
        home=Path(test_project.path),
    )

    # override config module project config
    monkeypatch.setattr("basic_memory.config.config", project_config)

    return project_config



@pytest.fixture(scope="function")
def app(app_config, project_config, engine_factory, test_project, project_session, config_manager) -> FastAPI:
    """Create test FastAPI application with single project."""

    app = fastapi_app
    app.dependency_overrides[get_project_config] = lambda: project_config
    app.dependency_overrides[get_engine_factory] = lambda: engine_factory
    app.dependency_overrides[get_app_config] = lambda: app_config
    return app


@pytest_asyncio.fixture(scope="function")
async def search_service(engine_factory, test_project):
    """Create and initialize search service for integration tests."""
    from basic_memory.repository.search_repository import SearchRepository
    from basic_memory.repository.entity_repository import EntityRepository
    from basic_memory.services.file_service import FileService
    from basic_memory.services.search_service import SearchService
    from basic_memory.markdown.markdown_processor import MarkdownProcessor
    from basic_memory.markdown import EntityParser

    engine, session_maker = engine_factory

    # Create repositories
    search_repository = SearchRepository(session_maker, project_id=test_project.id)
    entity_repository = EntityRepository(session_maker, project_id=test_project.id)

    # Create file service
    entity_parser = EntityParser(Path(test_project.path))
    markdown_processor = MarkdownProcessor(entity_parser)
    file_service = FileService(Path(test_project.path), markdown_processor)

    # Create and initialize search service
    service = SearchService(search_repository, entity_repository, file_service)
    await service.init_search_index()
    return service


@pytest.fixture(scope="function")
def mcp_server(app_config, search_service):
    # Import mcp instance
    from basic_memory.mcp.server import mcp as server

    # Import mcp tools to register them
    import basic_memory.mcp.tools  # noqa: F401

    # Import prompts to register them
    import basic_memory.mcp.prompts  # noqa: F401

    # Initialize project session with test project
    from basic_memory.mcp.project_session import session

    session.initialize(app_config.default_project)

    return server


@pytest_asyncio.fixture(scope="function")
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Create test client that both MCP and tests will use."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client