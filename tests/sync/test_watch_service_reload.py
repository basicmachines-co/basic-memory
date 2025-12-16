"""Tests for watch service project reloading functionality."""

import asyncio
from unittest.mock import AsyncMock, patch
import pytest

from basic_memory.config import BasicMemoryConfig
from basic_memory.models.project import Project
from basic_memory.sync.watch_service import WatchService


@pytest.mark.asyncio
async def test_schedule_restart_uses_config_interval():
    """Test that _schedule_restart uses the configured interval."""
    config = BasicMemoryConfig(watch_project_reload_interval=2)
    repo = AsyncMock()
    watch_service = WatchService(config, repo, quiet=True)

    stop_event = asyncio.Event()

    # Mock sleep to capture the interval
    with patch("asyncio.sleep") as mock_sleep:
        mock_sleep.return_value = None  # Make it return immediately

        await watch_service._schedule_restart(stop_event)

        # Verify sleep was called with config interval
        mock_sleep.assert_called_once_with(2)

        # Verify stop event was set
        assert stop_event.is_set()


@pytest.mark.asyncio
async def test_watch_projects_cycle_handles_empty_project_list():
    """Test that _watch_projects_cycle handles empty project list."""
    config = BasicMemoryConfig()
    repo = AsyncMock()
    watch_service = WatchService(config, repo, quiet=True)

    stop_event = asyncio.Event()
    stop_event.set()  # Set immediately to exit quickly

    # Mock awatch to track calls
    with patch("basic_memory.sync.watch_service.awatch") as mock_awatch:
        # Create an async iterator that yields nothing
        async def empty_iterator():
            return
            yield  # unreachable, just for async generator

        mock_awatch.return_value = empty_iterator()

        # Should not raise error with empty project list
        await watch_service._watch_projects_cycle([], stop_event)

        # awatch should be called with no paths
        mock_awatch.assert_called_once_with(
            debounce=config.sync_delay,
            watch_filter=watch_service.filter_changes,
            recursive=True,
            stop_event=stop_event,
        )


@pytest.mark.asyncio
async def test_run_handles_no_projects():
    """Test that run method handles no active projects gracefully."""
    config = BasicMemoryConfig()
    repo = AsyncMock()
    repo.get_active_projects.return_value = []  # No projects

    watch_service = WatchService(config, repo, quiet=True)

    call_count = 0

    def stop_after_one_call(*args):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            watch_service.state.running = False
        return AsyncMock()

    # Mock sleep and write_status to track behavior
    with patch("asyncio.sleep", side_effect=stop_after_one_call) as mock_sleep:
        with patch.object(watch_service, "write_status", return_value=None):
            await watch_service.run()

    # Should have slept for the configured reload interval when no projects found
    mock_sleep.assert_called_with(config.watch_project_reload_interval)


@pytest.mark.asyncio
async def test_run_reloads_projects_each_cycle():
    """Test that run method reloads projects in each cycle."""
    config = BasicMemoryConfig()
    repo = AsyncMock()

    # Return different projects on each call
    projects_call_1 = [Project(id=1, name="project1", path="/tmp/project1", permalink="project1")]
    projects_call_2 = [
        Project(id=1, name="project1", path="/tmp/project1", permalink="project1"),
        Project(id=2, name="project2", path="/tmp/project2", permalink="project2"),
    ]

    repo.get_active_projects.side_effect = [projects_call_1, projects_call_2]

    watch_service = WatchService(config, repo, quiet=True)

    cycle_count = 0

    async def mock_watch_cycle(projects, stop_event):
        nonlocal cycle_count
        cycle_count += 1
        if cycle_count >= 2:
            watch_service.state.running = False

    with patch.object(watch_service, "_watch_projects_cycle", side_effect=mock_watch_cycle):
        with patch.object(watch_service, "write_status", return_value=None):
            await watch_service.run()

    # Should have reloaded projects twice
    assert repo.get_active_projects.call_count == 2

    # Should have completed two cycles
    assert cycle_count == 2


@pytest.mark.asyncio
async def test_run_continues_after_cycle_error():
    """Test that run continues to next cycle after error in watch cycle."""
    config = BasicMemoryConfig()
    repo = AsyncMock()
    repo.get_active_projects.return_value = [
        Project(id=1, name="test", path="/tmp/test", permalink="test")
    ]

    watch_service = WatchService(config, repo, quiet=True)

    call_count = 0

    async def failing_watch_cycle(projects, stop_event):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("Simulated error")
        else:
            # Stop after second call
            watch_service.state.running = False

    with patch.object(watch_service, "_watch_projects_cycle", side_effect=failing_watch_cycle):
        with patch("asyncio.sleep") as mock_sleep:
            with patch.object(watch_service, "write_status", return_value=None):
                await watch_service.run()

    # Should have tried both cycles
    assert call_count == 2

    # Should have slept for error retry
    mock_sleep.assert_called_with(5)


@pytest.mark.asyncio
async def test_timer_task_cancelled_properly():
    """Test that timer task is cancelled when cycle completes."""
    config = BasicMemoryConfig()
    repo = AsyncMock()
    repo.get_active_projects.return_value = [
        Project(id=1, name="test", path="/tmp/test", permalink="test")
    ]

    watch_service = WatchService(config, repo, quiet=True)

    # Track created timer tasks
    created_tasks = []
    original_create_task = asyncio.create_task

    def track_create_task(coro):
        task = original_create_task(coro)
        created_tasks.append(task)
        return task

    async def quick_watch_cycle(projects, stop_event):
        # Complete immediately
        watch_service.state.running = False

    with patch("asyncio.create_task", side_effect=track_create_task):
        with patch.object(watch_service, "_watch_projects_cycle", side_effect=quick_watch_cycle):
            with patch.object(watch_service, "write_status", return_value=None):
                await watch_service.run()

    # Should have created one timer task
    assert len(created_tasks) == 1

    # Timer task should be cancelled or done
    timer_task = created_tasks[0]
    assert timer_task.cancelled() or timer_task.done()


@pytest.mark.asyncio
async def test_new_project_addition_scenario():
    """Test the main scenario: new project is detected when added while watching."""
    config = BasicMemoryConfig()
    repo = AsyncMock()

    # Initially one project
    initial_projects = [Project(id=1, name="existing", path="/tmp/existing", permalink="existing")]

    # After some time, new project is added
    updated_projects = [
        Project(id=1, name="existing", path="/tmp/existing", permalink="existing"),
        Project(id=2, name="new", path="/tmp/new", permalink="new"),
    ]

    # Track which project lists were used
    project_lists_used = []

    def mock_get_projects():
        if len(project_lists_used) < 2:
            project_lists_used.append(initial_projects)
            return initial_projects
        else:
            project_lists_used.append(updated_projects)
            return updated_projects

    repo.get_active_projects.side_effect = mock_get_projects

    watch_service = WatchService(config, repo, quiet=True)

    cycle_count = 0

    async def counting_watch_cycle(projects, stop_event):
        nonlocal cycle_count
        cycle_count += 1

        # Stop after enough cycles to test project reload
        if cycle_count >= 3:
            watch_service.state.running = False

    with patch.object(watch_service, "_watch_projects_cycle", side_effect=counting_watch_cycle):
        with patch.object(watch_service, "write_status", return_value=None):
            await watch_service.run()

    # Should have reloaded projects multiple times
    assert repo.get_active_projects.call_count >= 3

    # Should have completed multiple cycles
    assert cycle_count == 3

    # Should have seen both project configurations
    assert len(project_lists_used) >= 3
    assert any(len(projects) == 1 for projects in project_lists_used)  # Initial state
    assert any(len(projects) == 2 for projects in project_lists_used)  # After addition
