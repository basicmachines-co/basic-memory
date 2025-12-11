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
from typing import AsyncGenerator, Literal

import pytest
import pytest_asyncio
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer

from httpx import AsyncClient, ASGITransport

from basic_memory.config import BasicMemoryConfig, ProjectConfig, ConfigManager, DatabaseBackend
from basic_memory.db import engine_session_factory, DatabaseType
from basic_memory.models import Project
from basic_memory.models.base import Base
from basic_memory.repository.project_repository import ProjectRepository
from fastapi import FastAPI

from basic_memory.deps import get_project_config, get_engine_factory, get_app_config


# Import MCP tools so they're available for testing
from basic_memory.mcp import tools  # noqa: F401


# =============================================================================
# Database Backend Selection (env var approach)
# =============================================================================
# By default, integration tests run against SQLite.
# Set BASIC_MEMORY_TEST_POSTGRES=1 to run against Postgres (uses testcontainers).


@pytest.fixture(scope="session")
def db_backend() -> Literal["sqlite", "postgres"]:
    """Determine database backend from environment variable.

    Default: sqlite
    Set BASIC_MEMORY_TEST_POSTGRES=1 to use postgres
    """
    if os.environ.get("BASIC_MEMORY_TEST_POSTGRES", "").lower() in ("1", "true", "yes"):
        return "postgres"
    return "sqlite"


@pytest.fixture(scope="session")
def postgres_container(db_backend):
    """Session-scoped Postgres container for integration tests.

    Uses testcontainers to spin up a real Postgres instance.
    Only starts if db_backend is "postgres".
    """
    if db_backend != "postgres":
        yield None
        return

    with PostgresContainer("postgres:16-alpine") as postgres:
        yield postgres


@pytest_asyncio.fixture
async def engine_factory(
    app_config,
    config_manager,
    db_backend: Literal["sqlite", "postgres"],
    postgres_container,
    tmp_path,
) -> AsyncGenerator[tuple, None]:
    """Create engine and session factory for the configured database backend."""
    from basic_memory.models.search import (
        CREATE_SEARCH_INDEX,
        CREATE_POSTGRES_SEARCH_INDEX_TABLE,
        CREATE_POSTGRES_SEARCH_INDEX_FTS,
        CREATE_POSTGRES_SEARCH_INDEX_METADATA,
    )
    from basic_memory import db

    if db_backend == "postgres":
        # Postgres mode using testcontainers
        sync_url = postgres_container.get_connection_url()
        async_url = sync_url.replace("postgresql+psycopg2", "postgresql+asyncpg")

        engine = create_async_engine(
            async_url,
            echo=False,
            poolclass=NullPool,
        )

        session_maker = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

        # Drop and recreate all tables for test isolation
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS search_index CASCADE"))
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
            # asyncpg requires separate execute calls for each statement
            await conn.execute(CREATE_POSTGRES_SEARCH_INDEX_TABLE)
            await conn.execute(CREATE_POSTGRES_SEARCH_INDEX_FTS)
            await conn.execute(CREATE_POSTGRES_SEARCH_INDEX_METADATA)

        yield engine, session_maker

        await engine.dispose()

    else:
        # SQLite: Create fresh database (fast with tmp files)
        db_path = tmp_path / "test.db"
        db_type = DatabaseType.FILESYSTEM

        async with engine_session_factory(db_path, db_type) as (engine, session_maker):
            # Create all tables via ORM
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            # Drop any SearchIndex ORM table, then create FTS5 virtual table
            async with db.scoped_session(session_maker) as session:
                await session.execute(text("DROP TABLE IF EXISTS search_index"))
                await session.execute(CREATE_SEARCH_INDEX)
                await session.commit()

            yield engine, session_maker


@pytest_asyncio.fixture
async def test_project(config_home, engine_factory) -> Project:
    """Create a test project."""
    project_data = {
        "name": "test-project",
        "description": "Project used for integration tests",
        "path": str(config_home),
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
    # Set BASIC_MEMORY_HOME to the test directory
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))
    return tmp_path


@pytest.fixture
def app_config(
    config_home,
    db_backend: Literal["sqlite", "postgres"],
    postgres_container,
    tmp_path,
    monkeypatch,
) -> BasicMemoryConfig:
    """Create test app configuration."""
    # Disable cloud mode for CLI tests
    monkeypatch.setenv("BASIC_MEMORY_CLOUD_MODE", "false")

    # Create a basic config with test-project like unit tests do
    projects = {"test-project": str(config_home)}

    # Configure database backend based on env var
    if db_backend == "postgres":
        database_backend = DatabaseBackend.POSTGRES
        # Get URL from testcontainer and convert to asyncpg driver
        sync_url = postgres_container.get_connection_url()
        database_url = sync_url.replace("postgresql+psycopg2", "postgresql+asyncpg")
    else:
        database_backend = DatabaseBackend.SQLITE
        database_url = None

    app_config = BasicMemoryConfig(
        env="test",
        projects=projects,
        default_project="test-project",
        default_project_mode=False,  # Match real-world usage - tools must pass explicit project
        update_permalinks_on_move=True,
        cloud_mode=False,  # Explicitly disable cloud mode
        database_backend=database_backend,
        database_url=database_url,
    )
    return app_config


@pytest.fixture
def config_manager(app_config: BasicMemoryConfig, config_home) -> ConfigManager:
    # Invalidate config cache to ensure clean state for each test
    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None

    config_manager = ConfigManager()
    # Update its paths to use the test directory
    config_manager.config_dir = config_home / ".basic-memory"
    config_manager.config_file = config_manager.config_dir / "config.json"
    config_manager.config_dir.mkdir(parents=True, exist_ok=True)

    # Ensure the config file is written to disk
    config_manager.save_config(app_config)
    return config_manager


@pytest.fixture
def project_config(test_project):
    """Create test project configuration."""

    project_config = ProjectConfig(
        name=test_project.name,
        home=Path(test_project.path),
    )

    return project_config


@pytest.fixture
def app(app_config, project_config, engine_factory, test_project, config_manager) -> FastAPI:
    """Create test FastAPI application with single project."""

    # Import the FastAPI app AFTER the config_manager has written the test config to disk
    # This ensures that when the app's lifespan manager runs, it reads the correct test config
    from basic_memory.api.app import app as fastapi_app

    app = fastapi_app
    app.dependency_overrides[get_project_config] = lambda: project_config
    app.dependency_overrides[get_engine_factory] = lambda: engine_factory
    app.dependency_overrides[get_app_config] = lambda: app_config
    return app


@pytest_asyncio.fixture
async def search_service(engine_factory, test_project, app_config):
    """Create and initialize search service for integration tests.

    Uses app_config fixture to determine database backend - no patching needed.
    """
    from basic_memory.repository.entity_repository import EntityRepository
    from basic_memory.services.file_service import FileService
    from basic_memory.services.search_service import SearchService
    from basic_memory.markdown.markdown_processor import MarkdownProcessor
    from basic_memory.markdown import EntityParser

    from basic_memory.repository.search_repository import create_search_repository

    engine, session_maker = engine_factory

    # Use factory function to create appropriate search repository
    search_repository = create_search_repository(session_maker, project_id=test_project.id)

    entity_repository = EntityRepository(session_maker, project_id=test_project.id)

    # Create file service
    entity_parser = EntityParser(Path(test_project.path))
    markdown_processor = MarkdownProcessor(entity_parser)
    file_service = FileService(Path(test_project.path), markdown_processor)

    # Create and initialize search service
    service = SearchService(search_repository, entity_repository, file_service)
    await service.init_search_index()
    return service


@pytest.fixture
def mcp_server(config_manager, search_service):
    # Import mcp instance
    from basic_memory.mcp.server import mcp as server

    # Import mcp tools to register them
    import basic_memory.mcp.tools  # noqa: F401

    # Import prompts to register them
    import basic_memory.mcp.prompts  # noqa: F401

    return server


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Create test client that both MCP and tests will use."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
