"""Test proper handling of .tmp files during sync."""

import asyncio
from pathlib import Path

import pytest
from watchfiles import Change


async def create_test_file(path: Path, content: str = "test content") -> None:
    """Create a test file with given content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.mark.asyncio
async def test_temp_file_filter(watch_service, app_config, project_config, test_project):
    """Test that .tmp files are correctly filtered out."""
    # Test filter_changes method directly
    tmp_path = Path(test_project.path) / "test.tmp"
    assert not watch_service.filter_changes(Change.added, str(tmp_path))

    # Test with valid file
    valid_path = Path(test_project.path) / "test.md"
    assert watch_service.filter_changes(Change.added, str(valid_path))


@pytest.mark.asyncio
async def test_handle_tmp_files(watch_service, project_config, test_project, sync_service):
    """Test handling of .tmp files during sync process."""
    project_dir = Path(test_project.path)

    # Create a .tmp file - this simulates a file being written with write_file_atomic
    tmp_file = project_dir / "test.tmp"
    await create_test_file(tmp_file, "This is a temporary file")

    # Create the target final file
    final_file = project_dir / "test.md"
    await create_test_file(final_file, "This is the final file")

    # Setup changes that include both the .tmp and final file
    changes = {
        (Change.added, str(tmp_file)),
        (Change.added, str(final_file)),
    }

    # Handle changes
    await watch_service.handle_changes(test_project, changes)

    # Verify only the final file got an entity
    tmp_entity = await sync_service.entity_repository.get_by_file_path("test.tmp")
    final_entity = await sync_service.entity_repository.get_by_file_path("test.md")

    assert tmp_entity is None, "Temp file should not have an entity"
    assert final_entity is not None, "Final file should have an entity"


@pytest.mark.asyncio
async def test_atomic_write_tmp_file_handling(
    watch_service, project_config, test_project, sync_service
):
    """Test handling of file changes during atomic write operations."""
    project_dir = project_config.home

    # This test simulates the full atomic write process:
    # 1. First a .tmp file is created
    # 2. Then the .tmp file is renamed to the final file
    # 3. Both events are processed by the watch service

    # Setup file paths
    tmp_path = project_dir / "document.tmp"
    final_path = project_dir / "document.md"

    # Create mockup of the atomic write process
    await create_test_file(tmp_path, "Content for document")

    # First batch of changes - .tmp file created
    changes1 = {(Change.added, str(tmp_path))}

    # Process first batch
    await watch_service.handle_changes(test_project, changes1)

    # Now "replace" the temp file with the final file
    tmp_path.rename(final_path)

    # Second batch of changes - .tmp file deleted, final file added
    changes2 = {(Change.deleted, str(tmp_path)), (Change.added, str(final_path))}

    # Process second batch
    await watch_service.handle_changes(test_project, changes2)

    # Verify only the final file is in the database
    tmp_entity = await sync_service.entity_repository.get_by_file_path("document.tmp")
    final_entity = await sync_service.entity_repository.get_by_file_path("document.md")

    assert tmp_entity is None, "Temp file should not have an entity"
    assert final_entity is not None, "Final file should have an entity"

    # Check events
    new_events = [e for e in watch_service.state.recent_events if e.action == "new"]
    assert len(new_events) == 1
    assert new_events[0].path == "document.md"


@pytest.mark.asyncio
async def test_rapid_atomic_writes(watch_service, project_config, test_project, sync_service):
    """Test handling of rapid atomic writes to the same destination."""
    project_dir = Path(test_project.path)

    # This test simulates multiple rapid atomic writes to the same file:
    # 1. Several .tmp files are created one after another
    # 2. Each is then renamed to the same final file
    # 3. Events are batched and processed together

    # Setup file paths
    tmp1_path = project_dir / "document.1.tmp"
    tmp2_path = project_dir / "document.2.tmp"
    final_path = project_dir / "document.md"

    # Create multiple temp files that will be used in sequence
    await create_test_file(tmp1_path, "First version")
    await create_test_file(tmp2_path, "Second version")

    # Simulate the first atomic write
    tmp1_path.rename(final_path)

    # Brief pause to ensure file system registers the change
    await asyncio.sleep(0.1)

    # Read content to verify
    content1 = final_path.read_text(encoding="utf-8")
    assert content1 == "First version"

    # Simulate the second atomic write
    tmp2_path.rename(final_path)

    # Verify content was updated
    content2 = final_path.read_text(encoding="utf-8")
    assert content2 == "Second version"

    # Create a batch of changes that might arrive in mixed order
    changes = {
        (Change.added, str(tmp1_path)),
        (Change.deleted, str(tmp1_path)),
        (Change.added, str(tmp2_path)),
        (Change.deleted, str(tmp2_path)),
        (Change.added, str(final_path)),
        (Change.modified, str(final_path)),
    }

    # Process all changes
    await watch_service.handle_changes(test_project, changes)

    # Verify only the final file is in the database
    final_entity = await sync_service.entity_repository.get_by_file_path("document.md")
    assert final_entity is not None

    # Also verify no tmp entities were created
    tmp1_entity = await sync_service.entity_repository.get_by_file_path("document.1.tmp")
    tmp2_entity = await sync_service.entity_repository.get_by_file_path("document.2.tmp")
    assert tmp1_entity is None
    assert tmp2_entity is None
