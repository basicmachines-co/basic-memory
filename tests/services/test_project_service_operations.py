"""Additional tests for ProjectService operations."""

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from basic_memory import db
from basic_memory.services import project_service as project_service_module
from basic_memory.services.project_service import ProjectService


@pytest.mark.asyncio
async def test_get_project_from_database(project_service: ProjectService):
    """Test getting projects from the database."""
    # Generate unique project name for testing
    test_project_name = f"test-project-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        test_path = str(test_root / "test-project")

        # Make sure directory exists
        os.makedirs(test_path, exist_ok=True)

        try:
            # Add a project to the database
            project_data = {
                "name": test_project_name,
                "path": test_path,
                "permalink": test_project_name.lower().replace(" ", "-"),
                "is_active": True,
                "is_default": False,
            }
            async with db.scoped_session(project_service.session_maker) as session:
                await project_service.repository.create(session, project_data)

            # Verify we can get the project
            async with db.scoped_session(project_service.session_maker) as session:
                project = await project_service.repository.get_by_name(session, test_project_name)
            assert project is not None
            assert project.name == test_project_name
            assert project.path == test_path

        finally:
            # Clean up
            async with db.scoped_session(project_service.session_maker) as session:
                project = await project_service.repository.get_by_name(session, test_project_name)
                if project:
                    await project_service.repository.delete(session, project.id)


@pytest.mark.asyncio
async def test_add_project_to_config(project_service: ProjectService, config_manager):
    """Test adding a project to the config manager."""
    # Generate unique project name for testing
    test_project_name = f"config-project-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        test_path = test_root / "config-project"

        # Make sure directory exists
        test_path.mkdir(parents=True, exist_ok=True)

        try:
            # Add a project to config only (using ConfigManager directly)
            config_manager.add_project(test_project_name, str(test_path))

            # Verify it's in the config
            assert test_project_name in project_service.projects
            assert Path(project_service.projects[test_project_name]) == test_path

        finally:
            # Clean up
            if test_project_name in project_service.projects:
                config_manager.remove_project(test_project_name)


@pytest.mark.asyncio
async def test_remove_project_cleans_external_vectors_before_database_delete(
    project_service: ProjectService,
    monkeypatch,
):
    """Project removal must clean adapter storage before deleting SQL ownership."""
    project_name = f"external-vector-project-{os.urandom(4).hex()}"
    search_repository = SimpleNamespace(delete_project_vector_rows=AsyncMock())
    search_repository_factory = Mock(return_value=search_repository)
    service = ProjectService(
        repository=project_service.repository,
        session_maker=project_service.session_maker,
        file_service=project_service.file_service,
        search_repository_factory=search_repository_factory,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        async with db.scoped_session(service.session_maker) as session:
            project = await service.repository.create(
                session,
                {
                    "name": project_name,
                    "path": temp_dir,
                    "permalink": project_name,
                    "is_active": True,
                },
            )
            project_id = project.id

        original_delete = service.repository.delete

        async def delete_after_vector_cleanup(session, entity_id: int) -> bool:
            search_repository.delete_project_vector_rows.assert_awaited_once_with(
                strict_adapter_cleanup=True
            )
            return await original_delete(session, entity_id)

        monkeypatch.setattr(service.repository, "delete", delete_after_vector_cleanup)

        await service.remove_project(project_name)

    search_repository_factory.assert_called_once_with(project_id)
    search_repository.delete_project_vector_rows.assert_awaited_once_with(
        strict_adapter_cleanup=True
    )


@pytest.mark.asyncio
async def test_remove_project_composes_vector_cleanup_without_injected_factory(
    project_service: ProjectService,
    monkeypatch,
):
    """Legacy service construction must still preserve external vector ownership."""
    project_name = f"fallback-vector-project-{os.urandom(4).hex()}"
    search_repository = SimpleNamespace(delete_project_vector_rows=AsyncMock())
    create_search_repository = Mock(return_value=search_repository)
    monkeypatch.setattr(
        project_service_module,
        "create_search_repository",
        create_search_repository,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        async with db.scoped_session(project_service.session_maker) as session:
            project = await project_service.repository.create(
                session,
                {
                    "name": project_name,
                    "path": temp_dir,
                    "permalink": project_name,
                    "is_active": True,
                },
            )
            project_id = project.id

        await project_service.remove_project(project_name)

    create_search_repository.assert_called_once()
    assert (
        create_search_repository.call_args.kwargs["session_maker"] is project_service.session_maker
    )
    assert create_search_repository.call_args.kwargs["project_id"] == project_id
    search_repository.delete_project_vector_rows.assert_awaited_once_with(
        strict_adapter_cleanup=True
    )
