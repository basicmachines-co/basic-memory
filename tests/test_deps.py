"""Tests for dependency injection functions in the deps package."""

import pytest
from fastapi import FastAPI, HTTPException, Request

from basic_memory.api import container as container_module
from basic_memory.api.container import ApiContainer, resolve_container
from basic_memory.deps import get_app_config, validate_project_external_id
from basic_memory.models.project import Project
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.runtime.mode import resolve_runtime_mode


def _request_for(app: FastAPI) -> Request:
    return Request({"type": "http", "app": app})


def test_get_app_config_reads_lifespan_container(app_config):
    """API requests get the config the lifespan stored on app.state."""
    app = FastAPI()
    app.state.container = ApiContainer(
        config=app_config, mode=resolve_runtime_mode(is_test_env=True)
    )

    assert get_app_config(_request_for(app)) is app_config


def test_get_app_config_falls_back_to_composition_root(app_config, config_manager):
    """Requests without a lifespan (CLI/MCP local ASGI) resolve via the composition root."""
    app = FastAPI()

    resolved = get_app_config(_request_for(app))

    # resolve_container() reads the config the config_manager fixture wrote to disk.
    assert resolved.default_project == app_config.default_project
    assert resolved.projects == app_config.projects


def test_resolve_container_prefers_installed_container(app_config, monkeypatch):
    """A lifespan-installed container wins over creating a fresh one."""
    installed = ApiContainer(config=app_config, mode=resolve_runtime_mode(is_test_env=True))
    monkeypatch.setattr(container_module, "_container", installed)

    assert resolve_container() is installed


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
