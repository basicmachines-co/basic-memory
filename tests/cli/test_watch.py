"""Tests for the watch CLI command."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typer

from basic_memory.cli.commands.watch import run_watch
from basic_memory.config import BasicMemoryConfig


@pytest.fixture
def mock_config():
    """Create a mock config for testing."""
    return BasicMemoryConfig()


@pytest.fixture
def mock_container(mock_config):
    """Create a mock CLI container."""
    container = MagicMock()
    container.config = mock_config
    return container


class TestRunWatch:
    """Tests for run_watch async function."""

    @pytest.mark.asyncio
    async def test_initializes_app(self, mock_container):
        """run_watch calls initialize_app with the container's config."""
        mock_init = AsyncMock()

        with (
            patch("basic_memory.cli.commands.watch.get_container", return_value=mock_container),
            patch("basic_memory.cli.commands.watch.initialize_app", mock_init),
            patch("basic_memory.cli.commands.watch.SyncCoordinator") as mock_coordinator_cls,
            patch("basic_memory.cli.commands.watch.db") as mock_db,
        ):
            # Make coordinator.start() set the shutdown event so we don't block
            mock_coordinator = AsyncMock()
            mock_coordinator_cls.return_value = mock_coordinator

            async def start_then_shutdown():
                # Simulate immediate shutdown after start
                pass

            mock_coordinator.start = start_then_shutdown
            mock_coordinator.stop = AsyncMock()
            mock_db.shutdown_db = AsyncMock()

            # Patch signal handlers and make shutdown_event fire immediately
            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop_instance = MagicMock()
                mock_loop.return_value = mock_loop_instance

                # Capture the signal handler so we can trigger it
                signal_handlers = {}

                def capture_handler(sig, handler):
                    signal_handlers[sig] = handler

                mock_loop_instance.add_signal_handler.side_effect = capture_handler

                # Run in a task so we can trigger shutdown
                async def run_and_shutdown():
                    task = asyncio.create_task(run_watch())
                    # Give it a moment to start
                    await asyncio.sleep(0.01)
                    # Trigger shutdown via captured signal handler
                    import signal

                    if signal.SIGINT in signal_handlers:
                        signal_handlers[signal.SIGINT]()
                    await task

                await run_and_shutdown()

            mock_init.assert_called_once_with(mock_container.config)

    @pytest.mark.asyncio
    async def test_creates_coordinator_with_quiet_false(self, mock_container):
        """SyncCoordinator is created with should_sync=True and quiet=False."""
        with (
            patch("basic_memory.cli.commands.watch.get_container", return_value=mock_container),
            patch("basic_memory.cli.commands.watch.initialize_app", AsyncMock()),
            patch("basic_memory.cli.commands.watch.SyncCoordinator") as mock_coordinator_cls,
            patch("basic_memory.cli.commands.watch.db") as mock_db,
        ):
            mock_coordinator = AsyncMock()
            mock_coordinator_cls.return_value = mock_coordinator
            mock_db.shutdown_db = AsyncMock()

            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop_instance = MagicMock()
                mock_loop.return_value = mock_loop_instance

                signal_handlers = {}

                def capture_handler(sig, handler):
                    signal_handlers[sig] = handler

                mock_loop_instance.add_signal_handler.side_effect = capture_handler

                async def run_and_shutdown():
                    task = asyncio.create_task(run_watch())
                    await asyncio.sleep(0.01)
                    import signal

                    if signal.SIGINT in signal_handlers:
                        signal_handlers[signal.SIGINT]()
                    await task

                await run_and_shutdown()

            mock_coordinator_cls.assert_called_once_with(
                config=mock_container.config,
                should_sync=True,
                quiet=False,
            )

    @pytest.mark.asyncio
    async def test_project_sets_env_var(self, mock_container):
        """--project validates and sets BASIC_MEMORY_MCP_PROJECT env var."""
        mock_config_manager = MagicMock()
        mock_config_manager.get_project.return_value = ("my-project", "/some/path")

        with (
            patch("basic_memory.cli.commands.watch.get_container", return_value=mock_container),
            patch("basic_memory.cli.commands.watch.initialize_app", AsyncMock()),
            patch(
                "basic_memory.cli.commands.watch.ConfigManager",
                return_value=mock_config_manager,
            ),
            patch("basic_memory.cli.commands.watch.SyncCoordinator") as mock_coordinator_cls,
            patch("basic_memory.cli.commands.watch.db") as mock_db,
            patch.dict(os.environ, {}, clear=False),
        ):
            mock_coordinator = AsyncMock()
            mock_coordinator_cls.return_value = mock_coordinator
            mock_db.shutdown_db = AsyncMock()

            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop_instance = MagicMock()
                mock_loop.return_value = mock_loop_instance

                signal_handlers = {}

                def capture_handler(sig, handler):
                    signal_handlers[sig] = handler

                mock_loop_instance.add_signal_handler.side_effect = capture_handler

                async def run_and_shutdown():
                    task = asyncio.create_task(run_watch(project="my-project"))
                    await asyncio.sleep(0.01)
                    import signal

                    if signal.SIGINT in signal_handlers:
                        signal_handlers[signal.SIGINT]()
                    await task

                await run_and_shutdown()

            assert os.environ.get("BASIC_MEMORY_MCP_PROJECT") == "my-project"

        # Clean up env var
        os.environ.pop("BASIC_MEMORY_MCP_PROJECT", None)

    @pytest.mark.asyncio
    async def test_invalid_project_exits_with_error(self, mock_container):
        """--project with unknown name exits with error."""
        mock_config_manager = MagicMock()
        mock_config_manager.get_project.return_value = (None, None)

        with (
            patch("basic_memory.cli.commands.watch.get_container", return_value=mock_container),
            patch("basic_memory.cli.commands.watch.initialize_app", AsyncMock()),
            patch(
                "basic_memory.cli.commands.watch.ConfigManager",
                return_value=mock_config_manager,
            ),
        ):
            with pytest.raises(typer.Exit) as exc_info:
                await run_watch(project="nonexistent")

            assert exc_info.value.exit_code == 1

    @pytest.mark.asyncio
    async def test_shutdown_stops_coordinator_and_db(self, mock_container):
        """On shutdown, coordinator.stop() and db.shutdown_db() are called."""
        with (
            patch("basic_memory.cli.commands.watch.get_container", return_value=mock_container),
            patch("basic_memory.cli.commands.watch.initialize_app", AsyncMock()),
            patch("basic_memory.cli.commands.watch.SyncCoordinator") as mock_coordinator_cls,
            patch("basic_memory.cli.commands.watch.db") as mock_db,
        ):
            mock_coordinator = AsyncMock()
            mock_coordinator_cls.return_value = mock_coordinator
            mock_db.shutdown_db = AsyncMock()

            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop_instance = MagicMock()
                mock_loop.return_value = mock_loop_instance

                signal_handlers = {}

                def capture_handler(sig, handler):
                    signal_handlers[sig] = handler

                mock_loop_instance.add_signal_handler.side_effect = capture_handler

                async def run_and_shutdown():
                    task = asyncio.create_task(run_watch())
                    await asyncio.sleep(0.01)
                    import signal

                    if signal.SIGINT in signal_handlers:
                        signal_handlers[signal.SIGINT]()
                    await task

                await run_and_shutdown()

            mock_coordinator.stop.assert_called_once()
            mock_db.shutdown_db.assert_called_once()
