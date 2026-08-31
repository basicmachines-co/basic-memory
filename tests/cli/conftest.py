import os
from datetime import UTC, datetime
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from basic_memory.api.app import app as fastapi_app
from basic_memory.deps import get_engine_factory, get_app_config
from basic_memory.schemas.project_readiness import (
    ProjectIndexPhase,
    ProjectIndexReadiness,
    ProjectIndexStage,
    ProjectIndexStageName,
)


def make_readiness(
    phase: ProjectIndexPhase = ProjectIndexPhase.IDLE,
    *,
    files_on_disk: int = 0,
    indexed_entities: int = 0,
    files_pending: int = 0,
    files_total: int = 0,
) -> ProjectIndexReadiness:
    """Build a readiness value for tests that stub the status route's response."""
    return ProjectIndexReadiness(
        phase=phase,
        last_indexed_at=None if phase is ProjectIndexPhase.NEVER_INDEXED else datetime.now(UTC),
        files_on_disk=files_on_disk,
        indexed_entities=indexed_entities,
        stages=(
            ProjectIndexStage(
                name=ProjectIndexStageName.FILES,
                phase=phase,
                pending=files_pending,
                total=files_total,
            ),
            ProjectIndexStage(
                name=ProjectIndexStageName.RELATIONS, phase=phase, pending=0, total=0
            ),
            ProjectIndexStage(
                name=ProjectIndexStageName.EMBEDDINGS, phase=phase, pending=0, total=0
            ),
        ),
    )


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
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None

    monkeypatch.setenv("HOME", str(tmp_path))
    if os.name == "nt":
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # Trigger: a contributor runs the suite under a Claude profile wrapper.
    # Why: CLAUDE_CONFIG_DIR redirects the user-level settings the hook reads,
    # so an ambient value would point tests at their real config.
    # Outcome: unset it; tests that exercise it set it explicitly.
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    # Set to tmp_path directly (not tmp_path/basic-memory) so default project
    # home is tmp_path - tests expect to find imported files there
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path))
    return tmp_path


@pytest_asyncio.fixture
async def app(
    app_config, project_config, engine_factory, test_config, aiolib
) -> AsyncGenerator[FastAPI, None]:
    """Create test FastAPI application."""
    app = fastapi_app
    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_app_config] = lambda: app_config
    app.dependency_overrides[get_engine_factory] = lambda: engine_factory
    try:
        yield app
    finally:
        # Trigger: CLI tests share the module-level FastAPI app with API/MCP tests.
        # Why: leaving per-test dependency overrides installed lets later commands
        # talk to stale engines that no cleanup fixture owns.
        # Outcome: keep CLI app wiring isolated to the requesting test.
        app.dependency_overrides = previous_overrides


@pytest_asyncio.fixture
async def client(app: FastAPI, aiolib) -> AsyncGenerator[AsyncClient, None]:
    """Create test client that both MCP and tests will use."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def cli_env(project_config, client, test_config):
    """Set up CLI environment with correct project session."""
    return {"project_config": project_config, "client": client}
