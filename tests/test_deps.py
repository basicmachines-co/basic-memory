"""Tests for dependency injection functions in the deps package."""

import threading

import pytest
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient

from basic_memory.api import container as container_module
from basic_memory.api.container import ApiContainer, resolve_container
from basic_memory.deps import (
    AppConfigDep,
    ReadCacheDep,
    get_app_config,
    get_read_cache,
    validate_project_external_id,
)
from basic_memory.models.project import Project
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.runtime.mode import resolve_runtime_mode


def _request_for(app: FastAPI) -> Request:
    return Request({"type": "http", "app": app})


def _anyio_worker_threads() -> list[threading.Thread]:
    return [
        thread
        for thread in threading.enumerate()
        if thread.name == "AnyIO worker thread" and thread.is_alive()
    ]


@pytest.mark.asyncio
async def test_get_app_config_reads_lifespan_container(app_config):
    """API requests get the config the lifespan stored on app.state."""
    app = FastAPI()
    app.state.container = ApiContainer(
        config=app_config, mode=resolve_runtime_mode(is_test_env=True)
    )

    assert await get_app_config(_request_for(app)) is app_config


@pytest.mark.asyncio
async def test_get_app_config_falls_back_to_composition_root(app_config, config_manager):
    """Requests without a lifespan (CLI/MCP local ASGI) resolve via the composition root."""
    app = FastAPI()

    resolved = await get_app_config(_request_for(app))

    # resolve_container() reads the config the config_manager fixture wrote to disk.
    assert resolved.default_project == app_config.default_project
    assert resolved.projects == app_config.projects


def test_resolve_container_prefers_installed_container(app_config, monkeypatch):
    """A lifespan-installed container wins over creating a fresh one."""
    installed = ApiContainer(config=app_config, mode=resolve_runtime_mode(is_test_env=True))
    monkeypatch.setattr(container_module, "_container", installed)

    assert resolve_container() is installed


@pytest.mark.asyncio
async def test_get_read_cache_reads_lifespan_container(app_config):
    """API requests preserve the container's absent cache backend."""
    app = FastAPI()
    app.state.container = ApiContainer(
        config=app_config,
        mode=resolve_runtime_mode(is_test_env=True),
    )

    assert await get_read_cache(_request_for(app)) is None


@pytest.mark.asyncio
async def test_get_read_cache_falls_back_to_composition_root(app_config, monkeypatch):
    """Off-lifespan requests preserve the composition root's absent cache backend."""
    app = FastAPI()
    installed = ApiContainer(
        config=app_config,
        mode=resolve_runtime_mode(is_test_env=True),
    )
    monkeypatch.setattr(container_module, "_container", installed)

    assert await get_read_cache(_request_for(app)) is None


@pytest.mark.asyncio
async def test_config_and_read_cache_deps_resolve_on_the_event_loop(app_config):
    """Neither provider may hop to anyio's worker pool to read an attribute (#1345).

    A sync FastAPI dependency runs on a non-daemon worker thread that anyio parks
    for reuse, so a one-shot CLI process then depends on that thread being stopped
    at loop exit. Resolving on the loop removes the dependency on that path.
    """
    app = FastAPI()
    app.state.container = ApiContainer(
        config=app_config, mode=resolve_runtime_mode(is_test_env=True)
    )

    @app.get("/probe")
    async def probe(config: AppConfigDep, read_cache: ReadCacheDep) -> dict[str, str | None]:
        return {"default_project": config.default_project, "read_cache": repr(read_cache)}

    workers_before = len(_anyio_worker_threads())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/probe")

    assert response.status_code == 200
    assert response.json() == {"default_project": app_config.default_project, "read_cache": "None"}
    # Idle workers left by earlier tests may still be winding down, so only
    # growth is a regression.
    assert len(_anyio_worker_threads()) <= workers_before


@pytest.mark.asyncio
async def test_validate_project_external_id_success(
    project_repository: ProjectRepository, test_project: Project, session_maker
):
    """validate_project_external_id resolves the internal id from the external UUID."""
    project_id = await validate_project_external_id(
        session_maker=session_maker,
        project_id=test_project.external_id,
        project_repository=project_repository,
    )

    assert project_id == test_project.id


@pytest.mark.asyncio
async def test_validate_project_external_id_not_found(
    project_repository: ProjectRepository, session_maker
):
    """validate_project_external_id raises HTTPException when no project matches."""
    fake_uuid = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(HTTPException) as exc_info:
        await validate_project_external_id(
            session_maker=session_maker,
            project_id=fake_uuid,
            project_repository=project_repository,
        )

    assert exc_info.value.status_code == 404
    assert f"Project with external_id '{fake_uuid}' not found" in exc_info.value.detail
