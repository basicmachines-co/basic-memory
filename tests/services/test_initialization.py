"""Tests for the initialization service."""

from unittest.mock import patch

import pytest

from basic_memory.services.initialization import (
    ensure_initialization,
    initialize_app,
    initialize_database,
)


@pytest.mark.asyncio
@patch("basic_memory.services.initialization.db.run_migrations")
async def test_initialize_database(mock_run_migrations, test_config):
    """Test initializing the database."""
    await initialize_database(test_config)
    mock_run_migrations.assert_called_once_with(test_config)


@pytest.mark.asyncio
@patch("basic_memory.services.initialization.db.run_migrations")
async def test_initialize_database_error(mock_run_migrations, test_config):
    """Test handling errors during database initialization."""
    mock_run_migrations.side_effect = Exception("Test error")
    await initialize_database(test_config)
    mock_run_migrations.assert_called_once_with(test_config)


@pytest.mark.asyncio
@patch("basic_memory.services.initialization.initialize_database")
@patch("basic_memory.services.initialization.initialize_file_sync")
async def test_initialize_app(mock_initialize_file_sync, mock_initialize_database, test_config):
    """Test app initialization."""
    mock_initialize_file_sync.return_value = "task"

    result = await initialize_app(test_config)

    mock_initialize_database.assert_called_once_with(test_config)
    mock_initialize_file_sync.assert_called_once_with(test_config)
    assert result == "task"


@patch("basic_memory.services.initialization.asyncio.run")
def test_ensure_initialization(mock_run, test_config):
    """Test synchronous initialization wrapper."""
    ensure_initialization(test_config)
    mock_run.assert_called_once()
