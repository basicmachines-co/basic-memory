"""Tests for SyncCoordinator - centralized sync/watch lifecycle."""

import pytest
from unittest.mock import AsyncMock, patch

from basic_memory.config import BasicMemoryConfig
from basic_memory.sync.coordinator import SyncCoordinator, SyncStatus


class TestSyncCoordinator:
    """Test SyncCoordinator class."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock config for testing."""
        return BasicMemoryConfig()

    def test_initial_status(self, mock_config):
        """Coordinator starts in NOT_STARTED state."""
        coordinator = SyncCoordinator(config=mock_config)
        assert coordinator.status == SyncStatus.NOT_STARTED
        assert coordinator.is_running is False

    @pytest.mark.asyncio
    async def test_start_when_sync_disabled(self, mock_config):
        """When should_sync is False, start() sets status to STOPPED."""
        coordinator = SyncCoordinator(
            config=mock_config,
            should_sync=False,
            skip_reason="Test skip",
        )

        await coordinator.start()

        assert coordinator.status == SyncStatus.STOPPED
        assert coordinator.is_running is False

    @pytest.mark.asyncio
    async def test_stop_when_not_started(self, mock_config):
        """Stop is safe to call when not started."""
        coordinator = SyncCoordinator(config=mock_config)

        await coordinator.stop()  # Should not raise

        assert coordinator.status == SyncStatus.NOT_STARTED

    @pytest.mark.asyncio
    async def test_stop_when_stopped(self, mock_config):
        """Stop is idempotent when already stopped."""
        coordinator = SyncCoordinator(
            config=mock_config,
            should_sync=False,
        )
        await coordinator.start()  # Sets to STOPPED

        await coordinator.stop()  # Should not raise

        assert coordinator.status == SyncStatus.STOPPED

    def test_get_status_info(self, mock_config):
        """get_status_info returns diagnostic info."""
        coordinator = SyncCoordinator(
            config=mock_config,
            should_sync=True,
            skip_reason=None,
        )

        info = coordinator.get_status_info()

        assert info["status"] == "NOT_STARTED"
        assert info["should_sync"] is True
        assert info["skip_reason"] is None
        assert info["has_task"] is False

    def test_get_status_info_with_skip_reason(self, mock_config):
        """get_status_info includes skip reason."""
        coordinator = SyncCoordinator(
            config=mock_config,
            should_sync=False,
            skip_reason="Test environment detected",
        )

        info = coordinator.get_status_info()

        assert info["should_sync"] is False
        assert info["skip_reason"] == "Test environment detected"

    @pytest.mark.asyncio
    async def test_start_creates_task(self, mock_config):
        """When should_sync is True, start() creates a background task."""
        coordinator = SyncCoordinator(
            config=mock_config,
            should_sync=True,
        )

        # Mock initialize_file_sync to avoid actually starting sync
        # The import happens inside start(), so patch at the source module
        with patch(
            "basic_memory.services.initialization.initialize_file_sync",
            new_callable=AsyncMock,
        ):
            # Start coordinator
            await coordinator.start()

            # Should be running with a task
            assert coordinator.status == SyncStatus.RUNNING
            assert coordinator.is_running is True
            assert coordinator._sync_task is not None

            # Stop to clean up
            await coordinator.stop()

            assert coordinator.status == SyncStatus.STOPPED
            assert coordinator._sync_task is None

    @pytest.mark.asyncio
    async def test_start_already_running(self, mock_config):
        """Starting when already running is a no-op."""
        coordinator = SyncCoordinator(
            config=mock_config,
            should_sync=True,
        )

        with patch(
            "basic_memory.services.initialization.initialize_file_sync",
            new_callable=AsyncMock,
        ):
            await coordinator.start()
            first_task = coordinator._sync_task

            # Start again - should not create new task
            await coordinator.start()
            assert coordinator._sync_task is first_task

            await coordinator.stop()
