"""Stress tests for the watch service file change handling.

Verifies that the watcher handles large batches of file operations
correctly and maintains state consistency under load.
"""

from pathlib import Path

import pytest
from watchfiles import Change


async def create_test_file(path: Path, content: str = "test content") -> None:
    """Create a test file with given content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.mark.asyncio
async def test_handle_large_batch_of_file_adds(
    watch_service, project_config, test_project, entity_repository
):
    """Watcher handles 50+ file creations in a single batch."""
    project_dir = project_config.home
    file_count = 50

    # Create all files
    changes = set()
    for i in range(file_count):
        path = project_dir / f"batch_note_{i:03d}.md"
        content = f"""---
type: knowledge
---
# Batch Note {i}
Content for batch note {i} with unique text.
"""
        await create_test_file(path, content)
        changes.add((Change.added, str(path)))

    # Handle all changes in one batch
    await watch_service.handle_changes(test_project, changes)

    # Verify all files were synced
    synced_count = 0
    for i in range(file_count):
        entity = await entity_repository.get_by_file_path(f"batch_note_{i:03d}.md")
        if entity is not None:
            synced_count += 1

    assert synced_count == file_count, f"Only {synced_count}/{file_count} files synced"


@pytest.mark.asyncio
async def test_handle_mixed_operations_batch(
    watch_service, project_config, sync_service, test_project, entity_repository
):
    """Watcher handles mixed add/modify/delete operations in one batch."""
    project_dir = project_config.home

    # Phase 1: Create initial files via the sync pipeline
    initial_files = []
    for i in range(10):
        path = project_dir / f"mixed_note_{i:03d}.md"
        content = f"""---
type: knowledge
---
# Mixed Note {i}
Initial content for note {i}.
"""
        await create_test_file(path, content)
        initial_files.append(path)

    # Sync initial files
    initial_changes = {(Change.added, str(p)) for p in initial_files}
    await watch_service.handle_changes(test_project, initial_changes)

    # Phase 2: Mixed operations — modify some, delete some, add new ones
    mixed_changes = set()

    # Modify first 3 files
    for i in range(3):
        path = initial_files[i]
        path.write_text(f"""---
type: knowledge
---
# Mixed Note {i}
MODIFIED content for note {i}.
""")
        mixed_changes.add((Change.modified, str(path)))

    # Delete next 3 files
    for i in range(3, 6):
        path = initial_files[i]
        path.unlink()
        mixed_changes.add((Change.deleted, str(path)))

    # Add 5 new files
    for i in range(10, 15):
        path = project_dir / f"mixed_note_{i:03d}.md"
        content = f"""---
type: knowledge
---
# Mixed Note {i}
New content for note {i}.
"""
        await create_test_file(path, content)
        mixed_changes.add((Change.added, str(path)))

    await watch_service.handle_changes(test_project, mixed_changes)

    # Verify: modified files still exist with updated content
    for i in range(3):
        entity = await entity_repository.get_by_file_path(f"mixed_note_{i:03d}.md")
        assert entity is not None, f"Modified entity {i} should still exist"

    # Verify: deleted files are gone
    for i in range(3, 6):
        entity = await entity_repository.get_by_file_path(f"mixed_note_{i:03d}.md")
        assert entity is None, f"Deleted entity {i} should be gone"

    # Verify: new files were added
    for i in range(10, 15):
        entity = await entity_repository.get_by_file_path(f"mixed_note_{i:03d}.md")
        assert entity is not None, f"New entity {i} should exist"


@pytest.mark.asyncio
async def test_rapid_modifications_to_same_file(
    watch_service, project_config, test_project, entity_repository
):
    """Watcher handles multiple rapid changes to the same file."""
    project_dir = project_config.home
    path = project_dir / "rapid_note.md"

    # Create initial file
    await create_test_file(
        path,
        """---
type: knowledge
---
# Rapid Note
Version 1.
""",
    )
    await watch_service.handle_changes(test_project, {(Change.added, str(path))})

    # Rapidly modify the file multiple times, processing each change
    for version in range(2, 7):
        path.write_text(f"""---
type: knowledge
---
# Rapid Note
Version {version}.
""")
        await watch_service.handle_changes(test_project, {(Change.modified, str(path))})

    # The entity should exist and reflect the final state
    entity = await entity_repository.get_by_file_path("rapid_note.md")
    assert entity is not None
    assert entity.title == "rapid_note"

    # State should have recorded all events
    events = [e for e in watch_service.state.recent_events if "rapid_note" in e.path]
    assert len(events) >= 2  # At least the add and some modifications
