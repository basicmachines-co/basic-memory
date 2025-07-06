"""Tests for the initialization service."""

from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from basic_memory.services.initialization import (
    ensure_initialization,
    initialize_database,
    reconcile_projects_with_config,
    initialize_file_sync,
)


@pytest.mark.asyncio
@patch("basic_memory.services.initialization.db.get_or_create_db")
async def test_initialize_database(mock_get_or_create_db, app_config):
    """Test initializing the database."""
    mock_get_or_create_db.return_value = (MagicMock(), MagicMock())
    await initialize_database(app_config)
    mock_get_or_create_db.assert_called_once_with(app_config.database_path)


@pytest.mark.asyncio
@patch("basic_memory.services.initialization.db.get_or_create_db")
async def test_initialize_database_error(mock_get_or_create_db, app_config):
    """Test handling errors during database initialization."""
    mock_get_or_create_db.side_effect = Exception("Test error")
    await initialize_database(app_config)
    mock_get_or_create_db.assert_called_once_with(app_config.database_path)


@patch("basic_memory.services.initialization.asyncio.run")
def test_ensure_initialization(mock_run, project_config):
    """Test synchronous initialization wrapper."""
    ensure_initialization(project_config)
    mock_run.assert_called_once()


@pytest.mark.asyncio
@patch("basic_memory.services.initialization.db.get_or_create_db")
async def test_reconcile_projects_with_config(mock_get_db, app_config):
    """Test reconciling projects from config with database using ProjectService."""
    # Setup mocks
    mock_session_maker = AsyncMock()
    mock_get_db.return_value = (None, mock_session_maker)

    mock_repository = AsyncMock()
    mock_project_service = AsyncMock()
    mock_project_service.synchronize_projects = AsyncMock()

    # Mock the repository and project service
    with (
        patch("basic_memory.services.initialization.ProjectRepository") as mock_repo_class,
        patch(
            "basic_memory.services.project_service.ProjectService",
            return_value=mock_project_service,
        ),
    ):
        mock_repo_class.return_value = mock_repository

        # Set up app_config projects as a dictionary
        app_config.projects = {"test_project": "/path/to/project", "new_project": "/path/to/new"}
        app_config.default_project = "test_project"

        # Run the function
        await reconcile_projects_with_config(app_config)

        # Assertions
        mock_get_db.assert_called_once()
        mock_repo_class.assert_called_once_with(mock_session_maker)
        mock_project_service.synchronize_projects.assert_called_once()

        # We should no longer be calling these directly since we're using the service
        mock_repository.find_all.assert_not_called()
        mock_repository.set_as_default.assert_not_called()


@pytest.mark.asyncio
@patch("basic_memory.services.initialization.db.get_or_create_db")
async def test_reconcile_projects_with_error_handling(mock_get_db, app_config):
    """Test error handling during project synchronization."""
    # Setup mocks
    mock_session_maker = AsyncMock()
    mock_get_db.return_value = (None, mock_session_maker)

    mock_repository = AsyncMock()
    mock_project_service = AsyncMock()
    mock_project_service.synchronize_projects = AsyncMock(
        side_effect=ValueError("Project synchronization error")
    )

    # Mock the repository and project service
    with (
        patch("basic_memory.services.initialization.ProjectRepository") as mock_repo_class,
        patch(
            "basic_memory.services.project_service.ProjectService",
            return_value=mock_project_service,
        ),
        patch("basic_memory.services.initialization.logger") as mock_logger,
    ):
        mock_repo_class.return_value = mock_repository

        # Set up app_config projects as a dictionary
        app_config.projects = {"test_project": "/path/to/project"}
        app_config.default_project = "missing_project"

        # Run the function which now has error handling
        await reconcile_projects_with_config(app_config)

        # Assertions
        mock_get_db.assert_called_once()
        mock_repo_class.assert_called_once_with(mock_session_maker)
        mock_project_service.synchronize_projects.assert_called_once()

        # Verify error was logged
        mock_logger.error.assert_called_once_with(
            "Error during project synchronization: Project synchronization error"
        )
        mock_logger.info.assert_any_call(
            "Continuing with initialization despite synchronization error"
        )


@pytest.mark.asyncio
@patch("basic_memory.services.initialization.db.get_or_create_db")
@patch("basic_memory.cli.commands.sync.get_sync_service")
@patch("basic_memory.sync.WatchService")
async def test_initialize_file_sync_sequential(
    mock_watch_service_class, mock_get_sync_service, mock_get_db, app_config
):
    """Test file sync initialization with sequential project processing."""
    # Setup mocks
    mock_session_maker = AsyncMock()
    mock_get_db.return_value = (None, mock_session_maker)

    mock_watch_service = AsyncMock()
    mock_watch_service.run = AsyncMock()
    mock_watch_service_class.return_value = mock_watch_service

    mock_repository = AsyncMock()
    mock_project1 = MagicMock()
    mock_project1.name = "project1"
    mock_project1.path = "/path/to/project1"
    mock_project1.id = 1

    mock_project2 = MagicMock()
    mock_project2.name = "project2"
    mock_project2.path = "/path/to/project2"
    mock_project2.id = 2

    mock_sync_service = AsyncMock()
    mock_sync_service.sync = AsyncMock()
    mock_get_sync_service.return_value = mock_sync_service

    # Mock the repository
    with patch("basic_memory.services.initialization.ProjectRepository") as mock_repo_class:
        mock_repo_class.return_value = mock_repository
        mock_repository.get_active_projects.return_value = [mock_project1, mock_project2]

        # Run the function
        result = await initialize_file_sync(app_config)

        # Assertions
        mock_repository.get_active_projects.assert_called_once()

        # Should call sync for each project sequentially
        assert mock_get_sync_service.call_count == 2
        mock_get_sync_service.assert_any_call(mock_project1)
        mock_get_sync_service.assert_any_call(mock_project2)

        # Should call sync on each project
        assert mock_sync_service.sync.call_count == 2
        mock_sync_service.sync.assert_any_call(
            Path(mock_project1.path), project_name=mock_project1.name
        )
        mock_sync_service.sync.assert_any_call(
            Path(mock_project2.path), project_name=mock_project2.name
        )

        # Should start the watch service
        mock_watch_service.run.assert_called_once()

        # Should return None
        assert result is None
