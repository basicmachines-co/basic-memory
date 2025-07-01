"""Test sync status service functionality."""

import pytest
from basic_memory.services.sync_status_service import SyncStatusTracker, SyncStatus


@pytest.fixture
def sync_tracker():
    """Create a fresh sync status tracker for testing."""
    return SyncStatusTracker()


def test_sync_tracker_initial_state(sync_tracker):
    """Test initial state of sync tracker."""
    assert sync_tracker.is_ready
    assert not sync_tracker.is_syncing
    assert sync_tracker.global_status == SyncStatus.IDLE
    assert sync_tracker.get_summary() == "✅ System ready"

    # Test project-specific ready check for unknown project
    assert sync_tracker.is_project_ready("unknown-project")


def test_start_project_sync(sync_tracker):
    """Test starting project sync."""
    sync_tracker.start_project_sync("test-project", files_total=10)

    assert not sync_tracker.is_ready
    assert sync_tracker.is_syncing
    assert sync_tracker.global_status == SyncStatus.SYNCING

    project_status = sync_tracker.get_project_status("test-project")
    assert project_status is not None
    assert project_status.status == SyncStatus.SCANNING
    assert project_status.message == "Scanning files"
    assert project_status.files_total == 10


def test_update_project_progress(sync_tracker):
    """Test updating project progress."""
    sync_tracker.start_project_sync("test-project")  # Use default files_total=0
    sync_tracker.update_project_progress(
        "test-project", SyncStatus.SYNCING, "Processing files", files_processed=5, files_total=10
    )

    project_status = sync_tracker.get_project_status("test-project")
    assert project_status.status == SyncStatus.SYNCING
    assert project_status.message == "Processing files"
    assert project_status.files_processed == 5
    assert project_status.files_total == 10
    assert sync_tracker.global_status == SyncStatus.SYNCING


def test_complete_project_sync(sync_tracker):
    """Test completing project sync."""
    sync_tracker.start_project_sync("test-project")
    sync_tracker.complete_project_sync("test-project")

    assert sync_tracker.is_ready
    assert not sync_tracker.is_syncing
    assert sync_tracker.global_status == SyncStatus.COMPLETED

    project_status = sync_tracker.get_project_status("test-project")
    assert project_status.status == SyncStatus.COMPLETED
    assert project_status.message == "Sync completed"


def test_fail_project_sync(sync_tracker):
    """Test failing project sync."""
    sync_tracker.start_project_sync("test-project")
    sync_tracker.fail_project_sync("test-project", "Connection error")

    assert not sync_tracker.is_ready
    assert not sync_tracker.is_syncing
    assert sync_tracker.global_status == SyncStatus.FAILED

    project_status = sync_tracker.get_project_status("test-project")
    assert project_status.status == SyncStatus.FAILED
    assert project_status.error == "Connection error"


def test_start_project_watch(sync_tracker):
    """Test starting project watch mode."""
    sync_tracker.start_project_watch("test-project")

    assert sync_tracker.is_ready
    assert not sync_tracker.is_syncing
    assert sync_tracker.global_status == SyncStatus.COMPLETED

    project_status = sync_tracker.get_project_status("test-project")
    assert project_status.status == SyncStatus.WATCHING
    assert project_status.message == "Watching for changes"


def test_multiple_projects_status(sync_tracker):
    """Test status with multiple projects."""
    sync_tracker.start_project_sync("project1")
    sync_tracker.start_project_sync("project2")

    # Both scanning - should be syncing
    assert sync_tracker.global_status == SyncStatus.SYNCING
    assert sync_tracker.is_syncing

    # Complete one project
    sync_tracker.complete_project_sync("project1")
    assert sync_tracker.global_status == SyncStatus.SYNCING  # Still syncing

    # Complete second project
    sync_tracker.complete_project_sync("project2")
    assert sync_tracker.global_status == SyncStatus.COMPLETED
    assert sync_tracker.is_ready


def test_mixed_project_statuses(sync_tracker):
    """Test mixed project statuses."""
    sync_tracker.start_project_sync("project1")
    sync_tracker.start_project_sync("project2")

    # Fail one project
    sync_tracker.fail_project_sync("project1", "Error")
    # Complete other project
    sync_tracker.complete_project_sync("project2")

    # Should show failed status
    assert sync_tracker.global_status == SyncStatus.FAILED
    assert not sync_tracker.is_ready


def test_get_summary_with_progress(sync_tracker):
    """Test summary with progress information."""
    sync_tracker.start_project_sync("project1")
    sync_tracker.update_project_progress(
        "project1", SyncStatus.SYNCING, "Processing", files_processed=25, files_total=100
    )

    summary = sync_tracker.get_summary()
    assert "🔄 Syncing 1 projects" in summary
    assert "(25/100 files, 25%)" in summary


def test_get_all_projects(sync_tracker):
    """Test getting all project statuses."""
    sync_tracker.start_project_sync("project1")
    sync_tracker.start_project_sync("project2")

    all_projects = sync_tracker.get_all_projects()
    assert len(all_projects) == 2
    assert "project1" in all_projects
    assert "project2" in all_projects
    assert all_projects["project1"].status == SyncStatus.SCANNING
    assert all_projects["project2"].status == SyncStatus.SCANNING


def test_clear_completed(sync_tracker):
    """Test clearing completed project statuses."""
    sync_tracker.start_project_sync("project1")
    sync_tracker.start_project_sync("project2")

    sync_tracker.complete_project_sync("project1")
    sync_tracker.fail_project_sync("project2", "Error")

    # Should have 2 projects before clearing
    assert len(sync_tracker.get_all_projects()) == 2

    sync_tracker.clear_completed()

    # Should only have the failed project after clearing
    remaining = sync_tracker.get_all_projects()
    assert len(remaining) == 1
    assert "project2" in remaining
    assert remaining["project2"].status == SyncStatus.FAILED


def test_summary_messages(sync_tracker):
    """Test various summary messages."""
    # Initial state
    assert sync_tracker.get_summary() == "✅ System ready"

    # All completed
    sync_tracker.start_project_sync("project1")
    sync_tracker.complete_project_sync("project1")
    assert sync_tracker.get_summary() == "✅ All projects synced successfully"

    # Failed projects
    sync_tracker.fail_project_sync("project1", "Test error")
    assert "❌ Sync failed for: project1" in sync_tracker.get_summary()


def test_global_status_edge_cases(sync_tracker):
    """Test edge cases for global status calculation."""
    # Test mixed statuses (some completed, some watching) - should be completed
    sync_tracker.start_project_sync("project1")
    sync_tracker.start_project_sync("project2")

    sync_tracker.complete_project_sync("project1")
    sync_tracker.start_project_watch("project2")

    assert sync_tracker.global_status == SyncStatus.COMPLETED

    # Test fallback case - create a scenario that doesn't match specific conditions
    sync_tracker.start_project_sync("project3")
    sync_tracker.update_project_progress("project3", SyncStatus.IDLE, "Idle")

    # This should trigger the "else" clause in _update_global_status
    assert sync_tracker.global_status == SyncStatus.SYNCING


def test_summary_without_file_counts(sync_tracker):
    """Test summary when projects don't have file counts."""
    sync_tracker.start_project_sync("project1")  # files_total defaults to 0
    sync_tracker.start_project_sync("project2")  # files_total defaults to 0

    # Don't set file counts - should use the fallback message
    summary = sync_tracker.get_summary()
    assert "🔄 Syncing 2 projects" in summary
    assert "files" not in summary  # Should not show file progress


def test_is_project_ready_functionality(sync_tracker):
    """Test project-specific ready checks."""
    # Unknown project should be ready
    assert sync_tracker.is_project_ready("unknown-project")

    # Project in different states
    sync_tracker.start_project_sync("scanning-project")
    assert not sync_tracker.is_project_ready("scanning-project")  # SCANNING = not ready

    sync_tracker.update_project_progress("scanning-project", SyncStatus.SYNCING, "Processing")
    assert not sync_tracker.is_project_ready("scanning-project")  # SYNCING = not ready

    sync_tracker.fail_project_sync("scanning-project", "Test error")
    assert not sync_tracker.is_project_ready("scanning-project")  # FAILED = not ready

    sync_tracker.complete_project_sync("scanning-project")
    assert sync_tracker.is_project_ready("scanning-project")  # COMPLETED = ready

    # Test watching project
    sync_tracker.start_project_watch("watching-project")
    assert sync_tracker.is_project_ready("watching-project")  # WATCHING = ready


def test_project_isolation_scenario(sync_tracker):
    """Test the specific bug scenario: project isolation with mixed sync states."""
    # Set up the bug scenario: one failed project, one healthy project
    sync_tracker.start_project_sync("main")
    sync_tracker.fail_project_sync(
        "main", "UNIQUE constraint failed: entity.file_path, entity.project_id"
    )

    sync_tracker.start_project_sync("basic-memory-testing-20250626-1009")
    sync_tracker.complete_project_sync("basic-memory-testing-20250626-1009")
    sync_tracker.start_project_watch("basic-memory-testing-20250626-1009")

    # Global status should be failed due to "main" project
    assert sync_tracker.global_status == SyncStatus.FAILED
    assert not sync_tracker.is_ready

    # But the healthy project should be ready for operations
    assert sync_tracker.is_project_ready("basic-memory-testing-20250626-1009")
    assert not sync_tracker.is_project_ready("main")

    # This demonstrates the fix: project-specific checks allow isolation
