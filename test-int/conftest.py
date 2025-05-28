"""
Shared fixtures for integration tests.

These tests use the full Basic Memory stack including MCP server,
API endpoints, and database with realistic workflows.
"""

import tempfile
import pytest
import pytest_asyncio
from pathlib import Path

from basic_memory.config import BasicMemoryConfig, ProjectConfig
from basic_memory.db import engine_session_factory, DatabaseType
from basic_memory.models import Project
from basic_memory.repository.project_repository import ProjectRepository
from fastapi import FastAPI

from basic_memory.api.app import app as fastapi_app
from basic_memory.deps import get_project_config, get_engine_factory, get_app_config

# Import MCP tools so they're available for testing
from basic_memory.mcp import tools  # noqa: F401


@pytest.fixture(scope="function")
def tmp_project_path():
    """Create a temporary directory for test project."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield Path(tmp_dir)


@pytest_asyncio.fixture(scope="function")
async def engine_factory():
    """Create an in-memory SQLite engine factory for testing."""
    async with engine_session_factory(Path(":memory:"), DatabaseType.MEMORY) as (engine, session_maker):
        # Initialize database schema
        from basic_memory.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        
        # Return the tuple directly (like the regular tests do)
        yield engine, session_maker


@pytest_asyncio.fixture(scope="function")
async def test_project(tmp_project_path, engine_factory) -> Project:
    """Create a test project."""
    project_data = {
        "name": "test-project",
        "description": "Project used for integration tests",
        "path": str(tmp_project_path),
        "is_active": True,
        "is_default": True,
    }
    
    engine, session_maker = engine_factory
    project_repository = ProjectRepository(session_maker)
    project = await project_repository.create(project_data)
    return project


@pytest.fixture(scope="function")
def app_config(test_project) -> BasicMemoryConfig:
    """Create test app configuration."""
    projects = {test_project.name: str(test_project.path)}
    return BasicMemoryConfig(
        env="test",
        projects=projects,
        default_project=test_project.name
    )


@pytest.fixture(scope="function")
def project_config(test_project):
    """Create test project configuration."""
    return ProjectConfig(
        name=test_project.name,
        home=Path(test_project.path),
    )


@pytest.fixture(scope="function")
def app(app_config, project_config, engine_factory) -> FastAPI:
    """Create test FastAPI application with single project."""
    app = fastapi_app
    app.dependency_overrides[get_project_config] = lambda: project_config
    app.dependency_overrides[get_engine_factory] = lambda: engine_factory
    app.dependency_overrides[get_app_config] = lambda: app_config
    return app

