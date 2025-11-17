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

from typing import AsyncGenerator, Literal

import pytest
import pytest_asyncio
from pathlib import Path
from sqlalchemy import text

from httpx import AsyncClient, ASGITransport

from basic_memory.config import BasicMemoryConfig, ProjectConfig, ConfigManager, DatabaseBackend
from basic_memory.db import engine_session_factory, DatabaseType
from basic_memory.models import Project
from basic_memory.repository.project_repository import ProjectRepository
from fastapi import FastAPI

from basic_memory.deps import get_project_config, get_engine_factory, get_app_config


# Import MCP tools so they're available for testing
from basic_memory.mcp import tools  # noqa: F401


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", id="postgres", marks=pytest.mark.postgres),
    ]
)
def db_backend(request) -> Literal["sqlite", "postgres"]:
    """Parametrize tests to run against both SQLite and Postgres.

    Usage:
        pytest                          # Runs tests against SQLite only (default)
        pytest -m postgres              # Runs tests against Postgres only
        pytest -m "not postgres"        # Runs tests against SQLite only
        pytest --run-all-backends       # Runs tests against both backends

    Note: Only tests that use database fixtures (engine_factory, session_maker, etc.)
    will be parametrized. Tests that don't use the database won't be affected.
    """
    return request.param


# Module-level cache for Postgres schema setup (fast)
_POSTGRES_SCHEMA_INITIALIZED = False
_POSTGRES_ENGINE = None
_POSTGRES_SESSION_MAKER = None


@pytest_asyncio.fixture(scope="function")
async def engine_factory(
    app_config,
    config_manager,
    db_backend: Literal["sqlite", "postgres"],
    tmp_path,
) -> AsyncGenerator[tuple, None]:
    """Create engine and session factory for the configured database backend.

    For Postgres: Reuses cached schema, uses TRUNCATE for cleanup (fast - no migrations per test!)
    For SQLite: Creates fresh database per test (already fast with tmp files)
    """
    from basic_memory.models.search import CREATE_SEARCH_INDEX
    from basic_memory import db
    global _POSTGRES_SCHEMA_INITIALIZED, _POSTGRES_ENGINE, _POSTGRES_SESSION_MAKER

    # Determine database type based on backend
    if db_backend == "postgres":
        db_type = DatabaseType.FILESYSTEM
    else:
        db_type = DatabaseType.FILESYSTEM  # Integration tests use file-based SQLite

    # Use tmp_path for SQLite, use config database_path for Postgres
    if db_backend == "sqlite":
        db_path = tmp_path / "test.db"
    else:
        db_path = app_config.database_path

    if db_backend == "postgres":
        # Initialize schema once (cached across all tests)
        if not _POSTGRES_SCHEMA_INITIALIZED:
            # Ensure ConfigManager uses our test config
            config_manager._config = app_config

            # Create engine directly without context manager (so it doesn't get disposed)
            from basic_memory.db import _create_engine_and_session
            engine, session_maker = _create_engine_and_session(db_path, db_type)

            # Clean up any existing tables
            async with engine.begin() as conn:
                result = await conn.execute(text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                ))
                tables = [row[0] for row in result.fetchall()]
                for table in tables:
                    await conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))

            # Run migrations once for entire session
            from basic_memory.db import run_migrations
            await run_migrations(app_config, db_type)

            _POSTGRES_ENGINE = engine
            _POSTGRES_SESSION_MAKER = session_maker
            _POSTGRES_SCHEMA_INITIALIZED = True

        # Reuse cached engine/session_maker
        engine = _POSTGRES_ENGINE
        session_maker = _POSTGRES_SESSION_MAKER

        # Fast cleanup: TRUNCATE all tables (much faster than DROP/CREATE)
        async with engine.begin() as conn:
            # Disable foreign key checks temporarily
            await conn.execute(text("SET session_replication_role = 'replica'"))

            # Get all tables
            result = await conn.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ))
            tables = [row[0] for row in result.fetchall()]

            # TRUNCATE is much faster than DELETE
            for table in tables:
                await conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))

            # Re-enable foreign key checks
            await conn.execute(text("SET session_replication_role = 'origin'"))

        yield engine, session_maker

    else:
        # SQLite: Create fresh database (fast with tmp files)
        async with engine_session_factory(db_path, db_type) as (engine, session_maker):
            # Create all tables via ORM
            from basic_memory.models.base import Base
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            # Drop any SearchIndex ORM table, then create FTS5 virtual table
            async with db.scoped_session(session_maker) as session:
                await session.execute(text("DROP TABLE IF EXISTS search_index"))
                await session.execute(CREATE_SEARCH_INDEX)
                await session.commit()

            yield engine, session_maker


@pytest_asyncio.fixture(scope="function")
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


@pytest.fixture(scope="function")
def app_config(config_home, db_backend: Literal["sqlite", "postgres"], tmp_path, monkeypatch) -> BasicMemoryConfig:
    """Create test app configuration."""
    # Disable cloud mode for CLI tests
    monkeypatch.setenv("BASIC_MEMORY_CLOUD_MODE", "false")

    # Create a basic config with test-project like unit tests do
    projects = {"test-project": str(config_home)}

    # Configure database backend based on test parameter
    if db_backend == "postgres":
        database_backend = DatabaseBackend.POSTGRES
        database_url = "postgresql+asyncpg://basic_memory_user:dev_password@localhost:5433/basic_memory_test"
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


@pytest.fixture(scope="function")
def config_manager(app_config: BasicMemoryConfig, config_home) -> ConfigManager:
    config_manager = ConfigManager()
    # Update its paths to use the test directory
    config_manager.config_dir = config_home / ".basic-memory"
    config_manager.config_file = config_manager.config_dir / "config.json"
    config_manager.config_dir.mkdir(parents=True, exist_ok=True)

    # Ensure the config file is written to disk
    config_manager.save_config(app_config)
    return config_manager


@pytest.fixture(scope="function")
def project_config(test_project):
    """Create test project configuration."""

    project_config = ProjectConfig(
        name=test_project.name,
        home=Path(test_project.path),
    )

    return project_config


@pytest.fixture(scope="function")
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


@pytest_asyncio.fixture(scope="function")
async def search_service(engine_factory, test_project, app_config):
    """Create and initialize search service for integration tests."""
    from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
    from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
    from basic_memory.repository.entity_repository import EntityRepository
    from basic_memory.services.file_service import FileService
    from basic_memory.services.search_service import SearchService
    from basic_memory.markdown.markdown_processor import MarkdownProcessor
    from basic_memory.markdown import EntityParser

    engine, session_maker = engine_factory

    # Create backend-appropriate search repository
    if app_config.database_backend == DatabaseBackend.POSTGRES:
        search_repository = PostgresSearchRepository(session_maker, project_id=test_project.id)
    else:
        search_repository = SQLiteSearchRepository(session_maker, project_id=test_project.id)

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
def mcp_server(config_manager, search_service):
    # Import mcp instance
    from basic_memory.mcp.server import mcp as server

    # Import mcp tools to register them
    import basic_memory.mcp.tools  # noqa: F401

    # Import prompts to register them
    import basic_memory.mcp.prompts  # noqa: F401

    return server


@pytest_asyncio.fixture(scope="function")
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Create test client that both MCP and tests will use."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
