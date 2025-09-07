"""Test edge cases in the WatchService."""

from unittest.mock import patch

import pytest
from watchfiles import Change


def test_filter_changes_valid_path(watch_service, project_config):
    """Test the filter_changes method with valid non-hidden paths."""
    # Regular file path
    assert (
        watch_service.filter_changes(Change.added, str(project_config.home / "valid_file.txt"))
        is True
    )

    # Nested path
    assert (
        watch_service.filter_changes(
            Change.added, str(project_config.home / "nested" / "valid_file.txt")
        )
        is True
    )


def test_filter_changes_hidden_path(watch_service, project_config):
    """Test the filter_changes method with hidden files/directories."""
    # Hidden file (starts with dot)
    assert (
        watch_service.filter_changes(Change.added, str(project_config.home / ".hidden_file.txt"))
        is False
    )

    # File in hidden directory
    assert (
        watch_service.filter_changes(
            Change.added, str(project_config.home / ".hidden_dir" / "file.txt")
        )
        is False
    )

    # Deeply nested hidden directory
    assert (
        watch_service.filter_changes(
            Change.added, str(project_config.home / "valid" / ".hidden" / "file.txt")
        )
        is False
    )


@pytest.mark.asyncio
async def test_handle_changes_empty_set(watch_service, project_config, test_project):
    """Test handle_changes with an empty set (no processed files)."""
    # Mock write_status to avoid file operations
    with patch.object(watch_service, "write_status", return_value=None):
        # Capture console output to verify
        with patch.object(watch_service.console, "print") as mock_print:
            # Call handle_changes with empty set
            await watch_service.handle_changes(test_project, set())

            # Verify divider wasn't printed (processed is empty)
            mock_print.assert_not_called()

            # Verify last_scan was updated
            assert watch_service.state.last_scan is not None

            # Verify synced_files wasn't changed
            assert watch_service.state.synced_files == 0


@pytest.mark.asyncio
async def test_handle_vim_atomic_write_delete_still_exists(
    watch_service, project_config, test_project, sync_service
):
    """Test vim atomic write scenario: DELETE event but file still exists on disk."""
    project_dir = project_config.home

    # Create initial file and sync it
    test_file = project_dir / "vim_test.md"
    initial_content = """---
type: note
title: vim test
---
# Vim Test
Initial content for atomic write test
"""
    test_file.write_text(initial_content)
    await sync_service.sync(project_dir)

    # Get initial entity state
    initial_entity = await sync_service.entity_repository.get_by_file_path("vim_test.md")
    assert initial_entity is not None
    initial_checksum = initial_entity.checksum

    # Simulate vim's atomic write: modify content but send DELETE event
    # (vim moves original file, creates new content, then deletes old inode)
    modified_content = """---
type: note
title: vim test
---
# Vim Test
Modified content after atomic write
"""
    test_file.write_text(modified_content)

    # Setup DELETE event even though file still exists (vim's atomic write behavior)
    # Use absolute path like the real watch service would
    changes = {(Change.deleted, str(test_file))}

    # Handle the change
    await watch_service.handle_changes(test_project, changes)

    # Verify the entity still exists and was updated (not deleted)
    entity = await sync_service.entity_repository.get_by_file_path("vim_test.md")
    assert entity is not None
    assert entity.id == initial_entity.id  # Same entity
    assert entity.checksum != initial_checksum  # Checksum should be updated

    # Verify the file content was properly synced
    actual_content = test_file.read_text()
    assert "Modified content after atomic write" in actual_content

    # Check that correct event was recorded (should be "modified", not "deleted")
    events = [e for e in watch_service.state.recent_events if e.path == "vim_test.md"]
    assert len(events) == 1
    assert events[0].action == "modified"
    assert events[0].status == "success"


@pytest.mark.asyncio
async def test_handle_true_deletion_vs_vim_atomic(
    watch_service, project_config, test_project, sync_service
):
    """Test that true deletions are still handled correctly vs vim atomic writes."""
    project_dir = project_config.home

    # Create and sync two files
    atomic_file = project_dir / "atomic_test.md"
    delete_file = project_dir / "delete_test.md"

    content = """---
type: note
---
# Test File
Content for testing
"""

    atomic_file.write_text(content)
    delete_file.write_text(content)
    await sync_service.sync(project_dir)

    # For atomic_file: modify content but keep file (vim atomic write scenario)
    modified_content = content.replace("Content for testing", "Modified content")
    atomic_file.write_text(modified_content)

    # For delete_file: actually delete it (true deletion)
    delete_file.unlink()

    # Setup DELETE events for both files
    # Use absolute paths like the real watch service would
    changes = {
        (Change.deleted, str(atomic_file)),  # File still exists - atomic write
        (Change.deleted, str(delete_file)),  # File deleted - true deletion
    }

    # Handle the changes
    await watch_service.handle_changes(test_project, changes)

    # Verify atomic_file was treated as modification (still exists in DB)
    atomic_entity = await sync_service.entity_repository.get_by_file_path("atomic_test.md")
    assert atomic_entity is not None

    # Verify delete_file was truly deleted (no longer exists in DB)
    delete_entity = await sync_service.entity_repository.get_by_file_path("delete_test.md")
    assert delete_entity is None

    # Check events were recorded correctly
    events = watch_service.state.recent_events
    atomic_events = [e for e in events if e.path == "atomic_test.md"]
    delete_events = [e for e in events if e.path == "delete_test.md"]

    assert len(atomic_events) == 1
    assert atomic_events[0].action == "modified"

    assert len(delete_events) == 1
    assert delete_events[0].action == "deleted"


@pytest.mark.asyncio
async def test_handle_vim_atomic_write_markdown_with_relations(
    watch_service, project_config, test_project, sync_service
):
    """Test vim atomic write with markdown files that contain relations."""
    project_dir = project_config.home

    # Create target file for relations
    target_file = project_dir / "target.md"
    target_content = """---
type: note
title: Target Note
---
# Target Note
This is the target of relations.
"""
    target_file.write_text(target_content)

    # Create main file with relations
    main_file = project_dir / "main.md"
    initial_content = """---
type: note
title: Main Note
---
# Main Note
This note links to [[Target Note]].

- relates_to [[Target Note]]
"""
    main_file.write_text(initial_content)
    await sync_service.sync(project_dir)

    # Get initial state
    main_entity = await sync_service.entity_repository.get_by_file_path("main.md")
    assert main_entity is not None
    initial_relations = len(main_entity.relations)

    # Simulate vim atomic write with content change that adds more relations
    modified_content = """---
type: note
title: Main Note
---
# Main Note
This note links to [[Target Note]] multiple times.

- relates_to [[Target Note]]
- references [[Target Note]]
"""
    main_file.write_text(modified_content)

    # Setup DELETE event (vim atomic write)
    # Use absolute path like the real watch service would
    changes = {(Change.deleted, str(main_file))}

    # Handle the change
    await watch_service.handle_changes(test_project, changes)

    # Verify entity still exists and relations were updated
    updated_entity = await sync_service.entity_repository.get_by_file_path("main.md")
    assert updated_entity is not None
    assert updated_entity.id == main_entity.id

    # Verify relations were processed correctly
    updated_relations = len(updated_entity.relations)
    assert updated_relations >= initial_relations  # Should have at least as many relations

    # Check event was recorded as modification
    events = [e for e in watch_service.state.recent_events if e.path == "main.md"]
    assert len(events) == 1
    assert events[0].action == "modified"


@pytest.mark.asyncio
async def test_handle_vim_atomic_write_directory_path_ignored(
    watch_service, project_config, test_project
):
    """Test that directories are properly ignored even in atomic write detection."""
    project_dir = project_config.home

    # Create directory
    test_dir = project_dir / "test_directory"
    test_dir.mkdir()

    # Setup DELETE event for directory (should be ignored)
    # Use absolute path like the real watch service would
    changes = {(Change.deleted, str(test_dir))}

    # Handle the change - should not cause errors
    await watch_service.handle_changes(test_project, changes)

    # Verify no events were recorded for the directory
    events = [e for e in watch_service.state.recent_events if "test_directory" in e.path]
    assert len(events) == 0
