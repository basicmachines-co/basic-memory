"""Regression tests for cloud-only project info handling."""

import os

import pytest


@pytest.mark.asyncio
async def test_get_project_info_supports_db_only_project(
    project_service,
    project_repository,
    config_manager,
):
    """Project info should work when project exists in DB but not local config."""
    suffix = os.urandom(4).hex()
    project_name = f"cloud-only-{suffix}"
    project_path = f"/app/data/{project_name}"

    # Ensure the project is not present in local config.
    config = config_manager.load_config()
    config.projects.pop(project_name, None)
    config_manager.save_config(config)

    await project_repository.create(
        {
            "name": project_name,
            "path": project_path,
            "is_active": True,
            "is_default": False,
        }
    )

    info = await project_service.get_project_info(project_name)

    assert info.project_name == project_name
    assert info.project_path == project_path
    assert project_name in info.available_projects
    assert info.available_projects[project_name]["path"] == project_path
