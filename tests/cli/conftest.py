import os
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from basic_memory.api.app import app as fastapi_app
from basic_memory.deps import get_project_config, get_engine_factory, get_app_config


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch) -> Path:
    """Isolate tests from user's HOME directory.

    This prevents tests from reading/writing to ~/.basic-memory/.bmignore
    or other user-specific configuration.

    Sets BASIC_MEMORY_HOME to tmp_path directly so the default project
    writes files to tmp_path, which is where tests expect to find them.
    """
    # Clear config cache to ensure fresh config for each test
    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None

    monkeypatch.setenv("HOME", str(tmp_path))
    if os.name == "nt":
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # Set to tmp_path directly (not tmp_path/basic-memory) so default project
    # home is tmp_path - tests expect to find imported files there
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path))
    return tmp_path


@pytest_asyncio.fixture
async def app(app_config, project_config, engine_factory, test_config, aiolib) -> FastAPI:
    """Create test FastAPI application."""
    app = fastapi_app
    app.dependency_overrides[get_app_config] = lambda: app_config
    app.dependency_overrides[get_project_config] = lambda: project_config
    app.dependency_overrides[get_engine_factory] = lambda: engine_factory
    return app


@pytest_asyncio.fixture
async def client(app: FastAPI, aiolib) -> AsyncGenerator[AsyncClient, None]:
    """Create test client that both MCP and tests will use."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def cli_env(project_config, client, test_config):
    """Set up CLI environment with correct project session."""
    return {"project_config": project_config, "client": client}
