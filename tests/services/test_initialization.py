"""Tests for the initialization service."""

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
def test_ensure_initialization(mock_run, app_config):
    """Test synchronous initialization wrapper."""
    ensure_initialization(app_config)
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
@patch("basic_memory.sync.sync_service.get_sync_service")
@patch("basic_memory.sync.WatchService")
@patch("basic_memory.services.initialization.asyncio.create_task")
async def test_initialize_file_sync_background_tasks(
    mock_create_task, mock_watch_service_class, mock_get_sync_service, mock_get_db, app_config
):
    """Test file sync initialization with background task processing."""
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

    # Mock background tasks
    mock_task1 = MagicMock()
    mock_task2 = MagicMock()
    mock_create_task.side_effect = [mock_task1, mock_task2]

    # Mock the repository
    with patch("basic_memory.services.initialization.ProjectRepository") as mock_repo_class:
        mock_repo_class.return_value = mock_repository
        mock_repository.get_active_projects.return_value = [mock_project1, mock_project2]

        # Run the function
        result = await initialize_file_sync(app_config)

        # Assertions
        mock_repository.get_active_projects.assert_called_once()

        # Should create background tasks for each project (non-blocking)
        assert mock_create_task.call_count == 2

        # Verify tasks were created but not awaited (function returns immediately)
        assert result is None

        # Watch service should still be started
        mock_watch_service.run.assert_called_once()


@pytest.mark.asyncio
@patch("basic_memory.services.initialization.db.get_or_create_db")
@patch("basic_memory.sync.sync_service.get_sync_service")
@patch("basic_memory.sync.WatchService")
@patch("basic_memory.services.initialization.asyncio.create_task")
@patch.dict("os.environ", {"BASIC_MEMORY_MCP_PROJECT": "project1"})
async def test_initialize_file_sync_respects_project_constraint(
    mock_create_task, mock_watch_service_class, mock_get_sync_service, mock_get_db, app_config
):
    """Test that file sync only syncs the constrained project when BASIC_MEMORY_MCP_PROJECT is set."""
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

    mock_project3 = MagicMock()
    mock_project3.name = "project3"
    mock_project3.path = "/path/to/project3"
    mock_project3.id = 3

    mock_sync_service = AsyncMock()
    mock_sync_service.sync = AsyncMock()
    mock_get_sync_service.return_value = mock_sync_service

    # Mock background tasks
    mock_task = MagicMock()
    mock_create_task.return_value = mock_task

    # Mock the repository
    with patch("basic_memory.services.initialization.ProjectRepository") as mock_repo_class:
        mock_repo_class.return_value = mock_repository
        # Return all 3 projects from get_active_projects
        mock_repository.get_active_projects.return_value = [
            mock_project1,
            mock_project2,
            mock_project3,
        ]

        # Run the function
        result = await initialize_file_sync(app_config)

        # Assertions
        mock_repository.get_active_projects.assert_called_once()

        # Should only create 1 background task for project1 (the constrained project)
        assert mock_create_task.call_count == 1

        # Verify the function returns None
        assert result is None

        # Watch service should still be started
        mock_watch_service.run.assert_called_once()
