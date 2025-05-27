"""Tests for watch service."""

import asyncio
import json
from pathlib import Path

import pytest
from watchfiles import Change

from basic_memory.models.project import Project
from basic_memory.sync.watch_service import WatchService, WatchServiceState


async def create_test_file(path: Path, content: str = "test content") -> None:
    """Create a test file with given content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.fixture
def watch_service(sync_service, file_service, project_config):
    """Create watch service instance."""
    return WatchService(sync_service, file_service, project_config)


def test_watch_service_init(watch_service, project_config):
    """Test watch service initialization."""
    assert watch_service.status_path.parent.exists()


def test_state_add_event():
    """Test adding events to state."""
    state = WatchServiceState()
    event = state.add_event(path="test.md", action="new", status="success", checksum="abcd1234")

    assert len(state.recent_events) == 1
    assert state.recent_events[0] == event
    assert event.path == "test.md"
    assert event.action == "new"
    assert event.checksum == "abcd1234"

    # Test event limit
    for i in range(110):
        state.add_event(f"test{i}.md", "new", "success")
    assert len(state.recent_events) == 100


def test_state_record_error():
    """Test error recording in state."""
    state = WatchServiceState()
    state.record_error("test error")

    assert state.error_count == 1
    assert state.last_error is not None
    assert len(state.recent_events) == 1
    assert state.recent_events[0].action == "sync"
    assert state.recent_events[0].status == "error"
    assert state.recent_events[0].error == "test error"


@pytest.mark.asyncio
async def test_write_status(watch_service):
    """Test writing status file."""
    await watch_service.write_status()

    assert watch_service.status_path.exists()
    data = json.loads(watch_service.status_path.read_text(encoding="utf-8"))
    assert not data["running"]
    assert data["error_count"] == 0


@pytest.mark.asyncio
async def test_handle_file_add(watch_service, project_config, test_project, entity_repository):
    """Test handling new file creation."""
    project_dir = project_config.home

    # empty dir is ignored
    empty_dir = project_dir / "empty_dir"
    empty_dir.mkdir()

    # Setup changes
    new_file = project_dir / "new_note.md"
    changes = {(Change.added, str(empty_dir)), (Change.added, str(new_file))}

    # Create the file
    content = """---
type: knowledge
---
# New Note
Test content
"""
    await create_test_file(new_file, content)

    # Handle changes
    await watch_service.handle_changes(test_project, changes)

    # Verify
    entity = await entity_repository.get_by_file_path("new_note.md")
    assert entity is not None
    assert entity.title == "new_note"

    # Check event was recorded
    events = [e for e in watch_service.state.recent_events if e.action == "new"]
    assert len(events) == 1
    assert events[0].path == "new_note.md"
    assert events[0].status == "success"


@pytest.mark.asyncio
async def test_handle_file_modify(watch_service, project_config, sync_service, test_project):
    """Test handling file modifications."""
    project_dir = project_config.home

    # empty dir is ignored
    empty_dir = project_dir / "empty_dir"
    empty_dir.mkdir()

    # Create initial file
    test_file = project_dir / "test_note.md"
    initial_content = """---
type: knowledge
---
# Test Note
Initial content
"""
    await create_test_file(test_file, initial_content)

    # Initial sync
    await sync_service.sync(project_dir)

    # Modify file
    modified_content = """---
type: knowledge
---
# Test Note
Modified content
"""
    await create_test_file(test_file, modified_content)

    # Setup changes
    changes = {(Change.modified, str(empty_dir)), (Change.modified, str(test_file))}

    # Handle changes
    await watch_service.handle_changes(test_project, changes)

    # Verify
    entity = await sync_service.entity_repository.get_by_file_path("test_note.md")
    assert entity is not None

    # Check event was recorded
    events = [e for e in watch_service.state.recent_events if e.action == "modified"]
    assert len(events) == 1
    assert events[0].path == "test_note.md"
    assert events[0].status == "success"


@pytest.mark.asyncio
async def test_handle_file_delete(watch_service, project_config, test_project, sync_service):
    """Test handling file deletion."""
    project_dir = project_config.home

    # Create initial file
    test_file = project_dir / "to_delete.md"
    content = """---
type: knowledge
---
# Delete Test
Test content
"""
    await create_test_file(test_file, content)

    # Initial sync
    await sync_service.sync(project_dir)

    # Delete file
    test_file.unlink()

    # Setup changes
    changes = {(Change.deleted, str(test_file))}

    # Handle changes
    await watch_service.handle_changes(test_project, changes)

    # Verify
    entity = await sync_service.entity_repository.get_by_file_path("to_delete.md")
    assert entity is None

    # Check event was recorded
    events = [e for e in watch_service.state.recent_events if e.action == "deleted"]
    assert len(events) == 1
    assert events[0].path == "to_delete.md"
    assert events[0].status == "success"


@pytest.mark.asyncio
async def test_handle_file_move(watch_service, project_config, test_project, sync_service):
    """Test handling file moves."""
    project_dir = project_config.home

    # Create initial file
    old_path = project_dir / "old" / "test_move.md"
    content = """---
type: knowledge
---
# Move Test
Test content
"""
    await create_test_file(old_path, content)

    # Initial sync
    await sync_service.sync(project_dir)
    initial_entity = await sync_service.entity_repository.get_by_file_path("old/test_move.md")

    # Move file
    new_path = project_dir / "new" / "moved_file.md"
    new_path.parent.mkdir(parents=True)
    old_path.rename(new_path)

    # Setup changes
    changes = {(Change.deleted, str(old_path)), (Change.added, str(new_path))}

    # Handle changes
    await watch_service.handle_changes(test_project, changes)

    # Verify
    moved_entity = await sync_service.entity_repository.get_by_file_path("new/moved_file.md")
    assert moved_entity is not None
    assert moved_entity.id == initial_entity.id  # Same entity, new path

    # Original path should no longer exist
    old_entity = await sync_service.entity_repository.get_by_file_path("old/test_move.md")
    assert old_entity is None

    # Check event was recorded
    events = [e for e in watch_service.state.recent_events if e.action == "moved"]
    assert len(events) == 1
    assert events[0].path == "old/test_move.md -> new/moved_file.md"
    assert events[0].status == "success"


@pytest.mark.asyncio
async def test_handle_concurrent_changes(watch_service, project_config, test_project, sync_service):
    """Test handling multiple file changes happening close together."""
    project_dir = project_config.home

    # Create multiple files with small delays to simulate concurrent changes
    async def create_files():
        # Create first file
        file1 = project_dir / "note1.md"
        await create_test_file(file1, "First note")
        await asyncio.sleep(0.1)

        # Create second file
        file2 = project_dir / "note2.md"
        await create_test_file(file2, "Second note")
        await asyncio.sleep(0.1)

        # Modify first file
        await create_test_file(file1, "Modified first note")

        return file1, file2

    # Create files and collect changes
    file1, file2 = await create_files()

    # Setup combined changes
    changes = {
        (Change.added, str(file1)),
        (Change.modified, str(file1)),
        (Change.added, str(file2)),
    }

    # Handle changes
    await watch_service.handle_changes(test_project, changes)

    # Verify both files were processed
    entity1 = await sync_service.entity_repository.get_by_file_path("note1.md")
    entity2 = await sync_service.entity_repository.get_by_file_path("note2.md")

    assert entity1 is not None
    assert entity2 is not None

    # Check events were recorded in correct order
    events = watch_service.state.recent_events
    actions = [e.action for e in events]
    assert "new" in actions
    assert "modified" not in actions  # only process file once


@pytest.mark.asyncio
async def test_handle_rapid_move(watch_service, project_config, test_project, sync_service):
    """Test handling rapid move operations."""
    project_dir = project_config.home

    # Create initial file
    original_path = project_dir / "original.md"
    content = """---
type: knowledge
---
# Move Test
Test content for rapid moves
"""
    await create_test_file(original_path, content)
    await sync_service.sync(project_dir)

    # Perform rapid moves
    temp_path = project_dir / "temp.md"
    final_path = project_dir / "final.md"

    original_path.rename(temp_path)
    await asyncio.sleep(0.1)
    temp_path.rename(final_path)

    # Setup changes that might come in various orders
    changes = {
        (Change.deleted, str(original_path)),
        (Change.added, str(temp_path)),
        (Change.deleted, str(temp_path)),
        (Change.added, str(final_path)),
    }

    # Handle changes
    await watch_service.handle_changes(test_project, changes)

    # Verify final state
    final_entity = await sync_service.entity_repository.get_by_file_path("final.md")
    assert final_entity is not None

    # Intermediate paths should not exist
    original_entity = await sync_service.entity_repository.get_by_file_path("original.md")
    temp_entity = await sync_service.entity_repository.get_by_file_path("temp.md")
    assert original_entity is None
    assert temp_entity is None


@pytest.mark.asyncio
async def test_handle_delete_then_add(watch_service, project_config, test_project, sync_service):
    """Test handling rapid move operations."""
    project_dir = project_config.home

    # Create initial file
    original_path = project_dir / "original.md"
    content = """---
type: knowledge
---
# Move Test
Test content for rapid moves
"""
    await create_test_file(original_path, content)

    # Setup changes that might come in various orders
    changes = {
        (Change.deleted, str(original_path)),
        (Change.added, str(original_path)),
    }

    # Handle changes
    await watch_service.handle_changes(test_project, changes)

    # Verify final state
    original_entity = await sync_service.entity_repository.get_by_file_path("original.md")
    assert original_entity is None  # delete event is handled


@pytest.mark.asyncio
async def test_handle_directory_rename(watch_service, project_config, test_project, sync_service):
    """Test handling directory rename operations - regression test for the bug where directories
    were being processed as files, causing errors."""
    from unittest.mock import AsyncMock

    project_dir = project_config.home

    # Create a directory with a file inside
    old_dir_path = project_dir / "old_dir"
    old_dir_path.mkdir(parents=True, exist_ok=True)

    file_in_dir = old_dir_path / "test_file.md"
    content = """---
type: knowledge
---
# Test File
This is a test file in a directory
"""
    await create_test_file(file_in_dir, content)

    # Initial sync to add the file to the database
    await sync_service.sync(project_dir)

    # Rename the directory
    new_dir_path = project_dir / "new_dir"
    old_dir_path.rename(new_dir_path)

    # Setup changes that simulate directory rename
    # When a directory is renamed, watchfiles reports it as deleted and added
    changes = {
        (Change.deleted, str(old_dir_path)),
        (Change.added, str(new_dir_path)),
    }

    # Create a mocked version of sync_file to track calls
    original_sync_file = sync_service.sync_file
    mock_sync_file = AsyncMock(side_effect=original_sync_file)
    sync_service.sync_file = mock_sync_file

    # Handle changes - this should not throw an exception
    await watch_service.handle_changes(test_project, changes)

    # Check if our mock was called with any directory paths
    for call in mock_sync_file.call_args_list:
        args, kwargs = call
        path = args[0]
        full_path = project_dir / path
        assert not full_path.is_dir(), f"sync_file should not be called with directory path: {path}"

    # The file path should be untouched since we're ignoring directory events
    # We'd need a separate event for the file itself to be updated
    old_entity = await sync_service.entity_repository.get_by_file_path("old_dir/test_file.md")

    # The original entity should still exist since we only renamed the directory
    # but didn't process updates to the file itself
    assert old_entity is not None


def test_is_project_path(watch_service, tmp_path):
    """Test the is_project_path method to ensure it correctly identifies paths within a project."""
    # Create a project at a specific path
    project_path = tmp_path / "project"
    project_path.mkdir(parents=True, exist_ok=True)

    # Create a file inside the project
    file_in_project = project_path / "subdirectory" / "file.md"
    file_in_project.parent.mkdir(parents=True, exist_ok=True)
    file_in_project.touch()

    # Create a file outside the project
    file_outside_project = tmp_path / "outside" / "file.md"
    file_outside_project.parent.mkdir(parents=True, exist_ok=True)
    file_outside_project.touch()

    # Create Project object with our path
    project = Project(id=1, name="test", path=str(project_path), permalink="test")

    # Test a file inside the project
    assert watch_service.is_project_path(project, file_in_project) is True

    # Test a file outside the project
    assert watch_service.is_project_path(project, file_outside_project) is False

    # Test the project path itself
    assert watch_service.is_project_path(project, project_path) is False
