"""Tests for incremental scan watermark optimization (Phase 1.5).

These tests verify the scan watermark feature that dramatically improves sync
performance on large projects by:
- Using find -newermt for incremental scans (only changed files)
- Tracking last_scan_timestamp and last_file_count
- Falling back to full scan when deletions detected

Expected performance improvements:
- No changes: 225x faster (2s vs 450s for 1,460 files)
- Few changes: 84x faster (5s vs 420s)
"""

import time
from pathlib import Path
from textwrap import dedent

import pytest

from basic_memory.config import ProjectConfig
from basic_memory.sync.sync_service import SyncService


async def create_test_file(path: Path, content: str = "test content") -> None:
    """Create a test file with given content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


async def sleep_past_watermark(duration: float = 1.1) -> None:
    """Sleep long enough to ensure mtime is newer than watermark.

    Args:
        duration: Sleep duration in seconds (default 1.1s for filesystem precision)
    """
    time.sleep(duration)


# ==============================================================================
# Scan Strategy Selection Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_first_sync_uses_full_scan(sync_service: SyncService, project_config: ProjectConfig):
    """Test that first sync (no watermark) triggers full scan."""
    project_dir = project_config.home

    # Create test files
    await create_test_file(project_dir / "file1.md", "# File 1\nContent 1")
    await create_test_file(project_dir / "file2.md", "# File 2\nContent 2")

    # First sync - should use full scan (no watermark exists)
    report = await sync_service.sync(project_dir)

    assert len(report.new) == 2
    assert "file1.md" in report.new
    assert "file2.md" in report.new

    # Verify watermark was set
    project = await sync_service.project_repository.find_by_id(
        sync_service.entity_repository.project_id
    )
    assert project.last_scan_timestamp is not None
    assert project.last_file_count >= 2  # May include config files


@pytest.mark.asyncio
async def test_file_count_decreased_triggers_full_scan(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that file deletion (count decreased) triggers full scan."""
    project_dir = project_config.home

    # Create initial files
    await create_test_file(project_dir / "file1.md", "# File 1")
    await create_test_file(project_dir / "file2.md", "# File 2")
    await create_test_file(project_dir / "file3.md", "# File 3")

    # First sync
    await sync_service.sync(project_dir)

    # Delete a file
    (project_dir / "file2.md").unlink()

    # Sleep to ensure file operations complete
    await sleep_past_watermark()

    # Second sync - should detect deletion via full scan (file count decreased)
    report = await sync_service.sync(project_dir)

    assert len(report.deleted) == 1
    assert "file2.md" in report.deleted


@pytest.mark.asyncio
async def test_file_count_same_uses_incremental_scan(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that same file count uses incremental scan."""
    project_dir = project_config.home

    # Create initial files
    await create_test_file(project_dir / "file1.md", "# File 1\nOriginal")
    await create_test_file(project_dir / "file2.md", "# File 2\nOriginal")

    # First sync
    await sync_service.sync(project_dir)

    # Sleep to ensure mtime will be newer than watermark
    await sleep_past_watermark()

    # Modify one file (file count stays the same)
    await create_test_file(project_dir / "file1.md", "# File 1\nModified")

    # Second sync - should use incremental scan (same file count)
    report = await sync_service.sync(project_dir)

    assert len(report.modified) == 1
    assert "file1.md" in report.modified


@pytest.mark.asyncio
async def test_file_count_increased_uses_incremental_scan(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that increased file count still uses incremental scan."""
    project_dir = project_config.home

    # Create initial files
    await create_test_file(project_dir / "file1.md", "# File 1")
    await create_test_file(project_dir / "file2.md", "# File 2")

    # First sync
    await sync_service.sync(project_dir)

    # Sleep to ensure mtime will be newer than watermark
    await sleep_past_watermark()

    # Add a new file (file count increased)
    await create_test_file(project_dir / "file3.md", "# File 3")

    # Second sync - should use incremental scan and detect new file
    report = await sync_service.sync(project_dir)

    assert len(report.new) == 1
    assert "file3.md" in report.new


@pytest.mark.asyncio
async def test_force_full_bypasses_watermark_optimization(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that force_full=True bypasses watermark optimization and scans all files.

    This is critical for detecting changes made by external tools like rclone bisync
    that don't update mtimes detectably. See issue #407.
    """
    project_dir = project_config.home

    # Create initial files
    await create_test_file(project_dir / "file1.md", "# File 1\nOriginal")
    await create_test_file(project_dir / "file2.md", "# File 2\nOriginal")

    # First sync - establishes watermark
    report = await sync_service.sync(project_dir)
    assert len(report.new) == 2

    # Verify watermark was set
    project = await sync_service.project_repository.find_by_id(
        sync_service.entity_repository.project_id
    )
    assert project.last_scan_timestamp is not None
    initial_timestamp = project.last_scan_timestamp

    # Sleep to ensure time passes
    await sleep_past_watermark()

    # Modify a file WITHOUT updating mtime (simulates external tool like rclone)
    # We set mtime to be BEFORE the watermark to ensure incremental scan won't detect it
    file_path = project_dir / "file1.md"
    file_path.stat()
    await create_test_file(file_path, "# File 1\nModified by external tool")

    # Set mtime to be before the watermark (use time from before first sync)
    # This simulates rclone bisync which may preserve original timestamps
    import os

    old_time = initial_timestamp - 10  # 10 seconds before watermark
    os.utime(file_path, (old_time, old_time))

    # Normal incremental sync should NOT detect the change (mtime before watermark)
    report = await sync_service.sync(project_dir)
    assert len(report.modified) == 0, (
        "Incremental scan should not detect changes with mtime older than watermark"
    )

    # Force full scan should detect the change via checksum comparison
    report = await sync_service.sync(project_dir, force_full=True)
    assert len(report.modified) == 1, "Force full scan should detect changes via checksum"
    assert "file1.md" in report.modified

    # Verify watermark was still updated after force_full
    project = await sync_service.project_repository.find_by_id(
        sync_service.entity_repository.project_id
    )
    assert project.last_scan_timestamp is not None
    assert project.last_scan_timestamp > initial_timestamp


# ==============================================================================
# Incremental Scan Base Cases
# ==============================================================================


@pytest.mark.asyncio
async def test_incremental_scan_no_changes(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that incremental scan with no changes returns empty report."""
    project_dir = project_config.home

    # Create initial files
    await create_test_file(project_dir / "file1.md", "# File 1")
    await create_test_file(project_dir / "file2.md", "# File 2")

    # First sync
    await sync_service.sync(project_dir)

    # Sleep to ensure time passes
    await sleep_past_watermark()

    # Second sync - no changes
    report = await sync_service.sync(project_dir)

    assert len(report.new) == 0
    assert len(report.modified) == 0
    assert len(report.deleted) == 0
    assert len(report.moves) == 0


@pytest.mark.asyncio
async def test_incremental_scan_detects_new_file(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that incremental scan detects newly created files."""
    project_dir = project_config.home

    # Create initial file
    await create_test_file(project_dir / "file1.md", "# File 1")

    # First sync
    await sync_service.sync(project_dir)

    # Sleep to ensure mtime will be newer than watermark
    await sleep_past_watermark()

    # Create new file
    await create_test_file(project_dir / "file2.md", "# File 2")

    # Second sync - should detect new file via incremental scan
    report = await sync_service.sync(project_dir)

    assert len(report.new) == 1
    assert "file2.md" in report.new
    assert len(report.modified) == 0


@pytest.mark.asyncio
async def test_incremental_scan_detects_modified_file(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that incremental scan detects modified files."""
    project_dir = project_config.home

    # Create initial files
    file_path = project_dir / "file1.md"
    await create_test_file(file_path, "# File 1\nOriginal content")

    # First sync
    await sync_service.sync(project_dir)

    # Sleep to ensure mtime will be newer than watermark
    await sleep_past_watermark()

    # Modify the file
    await create_test_file(file_path, "# File 1\nModified content")

    # Second sync - should detect modification via incremental scan
    report = await sync_service.sync(project_dir)

    assert len(report.modified) == 1
    assert "file1.md" in report.modified
    assert len(report.new) == 0


@pytest.mark.asyncio
async def test_incremental_scan_detects_multiple_changes(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that incremental scan detects multiple file changes."""
    project_dir = project_config.home

    # Create initial files
    await create_test_file(project_dir / "file1.md", "# File 1\nOriginal")
    await create_test_file(project_dir / "file2.md", "# File 2\nOriginal")
    await create_test_file(project_dir / "file3.md", "# File 3\nOriginal")

    # First sync
    await sync_service.sync(project_dir)

    # Sleep to ensure mtime will be newer than watermark
    await sleep_past_watermark()

    # Modify multiple files
    await create_test_file(project_dir / "file1.md", "# File 1\nModified")
    await create_test_file(project_dir / "file3.md", "# File 3\nModified")
    await create_test_file(project_dir / "file4.md", "# File 4\nNew")

    # Second sync - should detect all changes via incremental scan
    report = await sync_service.sync(project_dir)

    assert len(report.modified) == 2
    assert "file1.md" in report.modified
    assert "file3.md" in report.modified
    assert len(report.new) == 1
    assert "file4.md" in report.new


# ==============================================================================
# Deletion Detection Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_deletion_triggers_full_scan_single_file(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that deleting a single file triggers full scan."""
    project_dir = project_config.home

    # Create initial files
    await create_test_file(project_dir / "file1.md", "# File 1")
    await create_test_file(project_dir / "file2.md", "# File 2")
    await create_test_file(project_dir / "file3.md", "# File 3")

    # First sync
    report1 = await sync_service.sync(project_dir)
    assert len(report1.new) == 3

    # Delete one file
    (project_dir / "file2.md").unlink()

    # Sleep to ensure file operations complete
    await sleep_past_watermark()

    # Second sync - should trigger full scan due to decreased file count
    report2 = await sync_service.sync(project_dir)

    assert len(report2.deleted) == 1
    assert "file2.md" in report2.deleted


@pytest.mark.asyncio
async def test_deletion_triggers_full_scan_multiple_files(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that deleting multiple files triggers full scan."""
    project_dir = project_config.home

    # Create initial files
    await create_test_file(project_dir / "file1.md", "# File 1")
    await create_test_file(project_dir / "file2.md", "# File 2")
    await create_test_file(project_dir / "file3.md", "# File 3")
    await create_test_file(project_dir / "file4.md", "# File 4")

    # First sync
    await sync_service.sync(project_dir)

    # Delete multiple files
    (project_dir / "file2.md").unlink()
    (project_dir / "file4.md").unlink()

    # Sleep to ensure file operations complete
    await sleep_past_watermark()

    # Second sync - should trigger full scan and detect both deletions
    report = await sync_service.sync(project_dir)

    assert len(report.deleted) == 2
    assert "file2.md" in report.deleted
    assert "file4.md" in report.deleted


# ==============================================================================
# Move Detection Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_move_detection_requires_full_scan(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that file moves require full scan to be detected (cannot detect in incremental).

    Moves (renames) don't update mtime, so incremental scans can't detect them.
    To trigger a full scan for move detection, we need file count to decrease.
    This test verifies moves are detected when combined with a deletion.
    """
    project_dir = project_config.home

    # Create initial files - include extra file to delete later
    old_path = project_dir / "old" / "file.md"
    content = dedent(
        """
        ---
        title: Test File
        type: note
        ---
        # Test File
        Distinctive content for move detection
        """
    ).strip()
    await create_test_file(old_path, content)
    await create_test_file(project_dir / "other.md", "# Other\nContent")

    # First sync
    await sync_service.sync(project_dir)

    # Sleep to ensure operations complete and watermark is in the past
    await sleep_past_watermark()

    # Move file AND delete another to trigger full scan
    # Move alone won't work because file count stays same (no full scan)
    new_path = project_dir / "new" / "moved.md"
    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.rename(new_path)
    (project_dir / "other.md").unlink()  # Delete to trigger full scan

    # Second sync - full scan due to deletion, move detected via checksum
    report = await sync_service.sync(project_dir)

    assert len(report.moves) == 1
    assert "old/file.md" in report.moves
    assert report.moves["old/file.md"] == "new/moved.md"
    assert len(report.deleted) == 1
    assert "other.md" in report.deleted


@pytest.mark.asyncio
async def test_move_detection_in_full_scan(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that file moves are detected via checksum in full scan."""
    project_dir = project_config.home

    # Create initial files
    old_path = project_dir / "old" / "file.md"
    content = dedent(
        """
        ---
        title: Test File
        type: note
        ---
        # Test File
        Distinctive content for move detection
        """
    ).strip()
    await create_test_file(old_path, content)
    await create_test_file(project_dir / "other.md", "# Other\nContent")

    # First sync
    await sync_service.sync(project_dir)

    # Sleep to ensure operations complete
    await sleep_past_watermark()

    # Move file AND delete another to trigger full scan
    new_path = project_dir / "new" / "moved.md"
    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.rename(new_path)
    (project_dir / "other.md").unlink()

    # Second sync - full scan due to deletion, should still detect move
    report = await sync_service.sync(project_dir)

    assert len(report.moves) == 1
    assert "old/file.md" in report.moves
    assert report.moves["old/file.md"] == "new/moved.md"
    assert len(report.deleted) == 1
    assert "other.md" in report.deleted


# ==============================================================================
# Watermark Update Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_watermark_updated_after_successful_sync(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that watermark is updated after each successful sync."""
    project_dir = project_config.home

    # Create initial file
    await create_test_file(project_dir / "file1.md", "# File 1")

    # Get project before sync
    project_before = await sync_service.project_repository.find_by_id(
        sync_service.entity_repository.project_id
    )
    assert project_before.last_scan_timestamp is None
    assert project_before.last_file_count is None

    # First sync
    sync_start = time.time()
    await sync_service.sync(project_dir)
    sync_end = time.time()

    # Verify watermark was set
    project_after = await sync_service.project_repository.find_by_id(
        sync_service.entity_repository.project_id
    )
    assert project_after.last_scan_timestamp is not None
    assert project_after.last_file_count >= 1  # May include config files

    # Watermark should be between sync start and end
    assert sync_start <= project_after.last_scan_timestamp <= sync_end


@pytest.mark.asyncio
async def test_watermark_uses_sync_start_time(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that watermark uses sync start time, not end time."""
    project_dir = project_config.home

    # Create initial file
    await create_test_file(project_dir / "file1.md", "# File 1")

    # First sync - capture timestamps
    sync_start = time.time()
    await sync_service.sync(project_dir)
    sync_end = time.time()

    # Get watermark
    project = await sync_service.project_repository.find_by_id(
        sync_service.entity_repository.project_id
    )

    # Watermark should be closer to start than end
    # (In practice, watermark == sync_start_timestamp captured in sync())
    time_from_start = abs(project.last_scan_timestamp - sync_start)
    time_from_end = abs(project.last_scan_timestamp - sync_end)

    assert time_from_start < time_from_end


@pytest.mark.asyncio
async def test_watermark_file_count_accurate(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that watermark file count accurately reflects synced files."""
    project_dir = project_config.home

    # Create initial files
    await create_test_file(project_dir / "file1.md", "# File 1")
    await create_test_file(project_dir / "file2.md", "# File 2")
    await create_test_file(project_dir / "file3.md", "# File 3")

    # First sync
    await sync_service.sync(project_dir)

    # Verify file count
    project1 = await sync_service.project_repository.find_by_id(
        sync_service.entity_repository.project_id
    )
    initial_count = project1.last_file_count
    assert initial_count >= 3  # May include config files

    # Add more files
    await sleep_past_watermark()
    await create_test_file(project_dir / "file4.md", "# File 4")
    await create_test_file(project_dir / "file5.md", "# File 5")

    # Second sync
    await sync_service.sync(project_dir)

    # Verify updated count increased by 2
    project2 = await sync_service.project_repository.find_by_id(
        sync_service.entity_repository.project_id
    )
    assert project2.last_file_count == initial_count + 2


# ==============================================================================
# Edge Cases and Error Handling
# ==============================================================================


@pytest.mark.asyncio
async def test_concurrent_file_changes_handled_gracefully(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that files created/modified during sync are handled correctly.

    Files created during sync (between start and file processing) should be
    caught in the next sync, not cause errors in the current sync.
    """
    project_dir = project_config.home

    # Create initial file
    await create_test_file(project_dir / "file1.md", "# File 1")

    # First sync
    await sync_service.sync(project_dir)

    # Sleep to ensure mtime will be newer
    await sleep_past_watermark()

    # Create file that will have mtime very close to watermark
    # In real scenarios, this could be created during sync
    await create_test_file(project_dir / "concurrent.md", "# Concurrent")

    # Should be caught in next sync without errors
    report = await sync_service.sync(project_dir)
    assert "concurrent.md" in report.new


@pytest.mark.asyncio
async def test_empty_directory_handles_incremental_scan(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that incremental scan handles empty directories correctly."""
    project_dir = project_config.home

    # First sync with empty directory (no user files)
    report1 = await sync_service.sync(project_dir)
    assert len(report1.new) == 0

    # Verify watermark was set even for empty directory
    project = await sync_service.project_repository.find_by_id(
        sync_service.entity_repository.project_id
    )
    assert project.last_scan_timestamp is not None
    # May have config files, so just check it's set
    assert project.last_file_count is not None

    # Second sync - still empty (no new user files)
    report2 = await sync_service.sync(project_dir)
    assert len(report2.new) == 0


@pytest.mark.asyncio
async def test_incremental_scan_respects_gitignore(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that incremental scan respects .gitignore patterns."""
    project_dir = project_config.home

    # Create .gitignore
    (project_dir / ".gitignore").write_text("*.ignored\n.hidden/\n")

    # Reload ignore patterns
    from basic_memory.ignore_utils import load_gitignore_patterns

    sync_service._ignore_patterns = load_gitignore_patterns(project_dir)

    # Create files - some should be ignored
    await create_test_file(project_dir / "included.md", "# Included")
    await create_test_file(project_dir / "excluded.ignored", "# Excluded")

    # First sync
    report1 = await sync_service.sync(project_dir)
    assert "included.md" in report1.new
    assert "excluded.ignored" not in report1.new

    # Sleep and add more files
    await sleep_past_watermark()
    await create_test_file(project_dir / "included2.md", "# Included 2")
    await create_test_file(project_dir / "excluded2.ignored", "# Excluded 2")

    # Second sync - incremental scan should also respect ignore patterns
    report2 = await sync_service.sync(project_dir)
    assert "included2.md" in report2.new
    assert "excluded2.ignored" not in report2.new


# ==============================================================================
# Relation Resolution Optimization Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_relation_resolution_skipped_when_no_changes(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that relation resolution is skipped when no file changes detected.

    This optimization prevents wasting time resolving relations when there are
    no changes, dramatically improving sync performance for large projects.
    """
    project_dir = project_config.home

    # Create initial file with wikilink
    content = dedent(
        """
        ---
        title: File with Link
        type: note
        ---
        # File with Link
        This links to [[Target File]]
        """
    ).strip()
    await create_test_file(project_dir / "file1.md", content)

    # First sync - will resolve relations (or leave unresolved)
    report1 = await sync_service.sync(project_dir)
    assert len(report1.new) == 1

    # Check that there are unresolved relations (target doesn't exist)
    unresolved = await sync_service.relation_repository.find_unresolved_relations()
    unresolved_count_before = len(unresolved)
    assert unresolved_count_before > 0  # Should have unresolved relation to [[Target File]]

    # Sleep to ensure time passes
    await sleep_past_watermark()

    # Second sync - no changes, should skip relation resolution
    report2 = await sync_service.sync(project_dir)
    assert report2.total == 0  # No changes detected

    # Verify unresolved relations count unchanged (resolution was skipped)
    unresolved_after = await sync_service.relation_repository.find_unresolved_relations()
    assert len(unresolved_after) == unresolved_count_before


@pytest.mark.asyncio
async def test_relation_resolution_runs_when_files_modified(
    sync_service: SyncService, project_config: ProjectConfig
):
    """Test that relation resolution runs when files are actually modified."""
    project_dir = project_config.home

    # Create file with unresolved wikilink
    content1 = dedent(
        """
        ---
        title: File with Link
        type: note
        ---
        # File with Link
        This links to [[Target File]]
        """
    ).strip()
    await create_test_file(project_dir / "file1.md", content1)

    # First sync
    await sync_service.sync(project_dir)

    # Verify unresolved relation exists
    unresolved_before = await sync_service.relation_repository.find_unresolved_relations()
    assert len(unresolved_before) > 0

    # Sleep to ensure mtime will be newer
    await sleep_past_watermark()

    # Create the target file (should resolve the relation)
    content2 = dedent(
        """
        ---
        title: Target File
        type: note
        ---
        # Target File
        This is the target.
        """
    ).strip()
    await create_test_file(project_dir / "target.md", content2)

    # Second sync - should detect new file and resolve relations
    report = await sync_service.sync(project_dir)
    assert len(report.new) == 1
    assert "target.md" in report.new

    # Verify relation was resolved (unresolved count decreased)
    unresolved_after = await sync_service.relation_repository.find_unresolved_relations()
    assert len(unresolved_after) < len(unresolved_before)
