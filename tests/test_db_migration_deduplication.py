"""Tests for database migration deduplication functionality."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from basic_memory import db


@pytest.fixture
def mock_alembic_config():
    """Mock Alembic config to avoid actual migration runs."""
    with patch("basic_memory.db.Config") as mock_config_class:
        mock_config = MagicMock()
        mock_config_class.return_value = mock_config
        yield mock_config


@pytest.fixture
def mock_alembic_command():
    """Mock Alembic command to avoid actual migration runs."""
    with patch("basic_memory.db.command") as mock_command:
        yield mock_command


@pytest.fixture
def mock_search_repository():
    """Mock SearchRepository to avoid database dependencies."""
    with patch("basic_memory.db.SearchRepository") as mock_repo_class:
        mock_repo = AsyncMock()
        mock_repo_class.return_value = mock_repo
        yield mock_repo


# Use the app_config fixture from conftest.py


@pytest.mark.asyncio
async def test_migration_deduplication_single_call(
    app_config, mock_alembic_config, mock_alembic_command, mock_search_repository
):
    """Test that migrations are only run once when called multiple times."""
    # Reset module state
    db._migrations_completed = False
    db._engine = None
    db._session_maker = None

    # First call should run migrations
    await db.run_migrations(app_config)

    # Verify migrations were called
    mock_alembic_command.upgrade.assert_called_once_with(mock_alembic_config, "head")
    mock_search_repository.init_search_index.assert_called_once()

    # Reset mocks for second call
    mock_alembic_command.reset_mock()
    mock_search_repository.reset_mock()

    # Second call should skip migrations
    await db.run_migrations(app_config)

    # Verify migrations were NOT called again
    mock_alembic_command.upgrade.assert_not_called()
    mock_search_repository.init_search_index.assert_not_called()


@pytest.mark.asyncio
async def test_migration_force_parameter(
    app_config, mock_alembic_config, mock_alembic_command, mock_search_repository
):
    """Test that migrations can be forced to run even if already completed."""
    # Reset module state
    db._migrations_completed = False
    db._engine = None
    db._session_maker = None

    # First call should run migrations
    await db.run_migrations(app_config)

    # Verify migrations were called
    mock_alembic_command.upgrade.assert_called_once_with(mock_alembic_config, "head")
    mock_search_repository.init_search_index.assert_called_once()

    # Reset mocks for forced call
    mock_alembic_command.reset_mock()
    mock_search_repository.reset_mock()

    # Forced call should run migrations again
    await db.run_migrations(app_config, force=True)

    # Verify migrations were called again
    mock_alembic_command.upgrade.assert_called_once_with(mock_alembic_config, "head")
    mock_search_repository.init_search_index.assert_called_once()


@pytest.mark.asyncio
async def test_migration_state_reset_on_shutdown():
    """Test that migration state is reset when database is shut down."""
    # Set up completed state
    db._migrations_completed = True
    db._engine = AsyncMock()
    db._session_maker = AsyncMock()

    # Shutdown should reset state
    await db.shutdown_db()

    # Verify state was reset
    assert db._migrations_completed is False
    assert db._engine is None
    assert db._session_maker is None


@pytest.mark.asyncio
async def test_get_or_create_db_runs_migrations_automatically(
    app_config, mock_alembic_config, mock_alembic_command, mock_search_repository
):
    """Test that get_or_create_db runs migrations automatically."""
    # Reset module state
    db._migrations_completed = False
    db._engine = None
    db._session_maker = None

    # First call should create engine and run migrations
    engine, session_maker = await db.get_or_create_db(app_config.database_path)

    # Verify we got valid objects
    assert engine is not None
    assert session_maker is not None

    # Verify migrations were called
    mock_alembic_command.upgrade.assert_called_once_with(mock_alembic_config, "head")
    mock_search_repository.init_search_index.assert_called_once()


@pytest.mark.asyncio
async def test_get_or_create_db_skips_migrations_when_disabled(
    app_config, mock_alembic_config, mock_alembic_command, mock_search_repository
):
    """Test that get_or_create_db can skip migrations when ensure_migrations=False."""
    # Reset module state
    db._migrations_completed = False
    db._engine = None
    db._session_maker = None

    # Call with ensure_migrations=False should skip migrations
    engine, session_maker = await db.get_or_create_db(
        app_config.database_path, ensure_migrations=False
    )

    # Verify we got valid objects
    assert engine is not None
    assert session_maker is not None

    # Verify migrations were NOT called
    mock_alembic_command.upgrade.assert_not_called()
    mock_search_repository.init_search_index.assert_not_called()


@pytest.mark.asyncio
async def test_multiple_get_or_create_db_calls_deduplicated(
    app_config, mock_alembic_config, mock_alembic_command, mock_search_repository
):
    """Test that multiple get_or_create_db calls only run migrations once."""
    # Reset module state
    db._migrations_completed = False
    db._engine = None
    db._session_maker = None

    # First call should create engine and run migrations
    await db.get_or_create_db(app_config.database_path)

    # Verify migrations were called
    mock_alembic_command.upgrade.assert_called_once_with(mock_alembic_config, "head")
    mock_search_repository.init_search_index.assert_called_once()

    # Reset mocks for subsequent calls
    mock_alembic_command.reset_mock()
    mock_search_repository.reset_mock()

    # Subsequent calls should not run migrations again
    await db.get_or_create_db(app_config.database_path)
    await db.get_or_create_db(app_config.database_path)

    # Verify migrations were NOT called again
    mock_alembic_command.upgrade.assert_not_called()
    mock_search_repository.init_search_index.assert_not_called()
