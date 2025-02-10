"""Tests for CLI sync command."""

import asyncio
from pathlib import Path
import pytest
import pytest_asyncio
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.cli.commands.sync import (
    display_validation_errors,
    display_sync_summary,
    display_detailed_sync_results,
    run_sync,
    group_issues_by_directory,
    ValidationIssue,
    get_sync_service
)
from basic_memory.sync.utils import SyncReport

# Set up CLI runner
runner = CliRunner()


@pytest_asyncio.fixture
async def sync_service(session_maker):
    """Create sync service instance with test database."""
    return await get_sync_service()


def test_group_issues_by_directory():
    """Test grouping validation issues by directory."""
    issues = [
        ValidationIssue("dir1/file1.md", "error1"),
        ValidationIssue("dir1/file2.md", "error2"),
        ValidationIssue("dir2/file3.md", "error3"),
    ]
    
    grouped = group_issues_by_directory(issues)
    
    assert len(grouped["dir1"]) == 2
    assert len(grouped["dir2"]) == 1
    assert grouped["dir1"][0].error == "error1"
    assert grouped["dir2"][0].error == "error3"


def test_display_validation_errors():
    """Test displaying validation errors."""
    issues = [
        ValidationIssue("dir1/file1.md", "Missing title"),
        ValidationIssue("dir1/file2.md", "Invalid date"),
        ValidationIssue("dir2/file3.md", "Missing tags"),
    ]
    
    # Just verify it runs without error
    display_validation_errors(issues)


def test_display_sync_summary_no_changes():
    """Test displaying sync summary with no changes."""
    changes = SyncReport(set(), set(), set(), {}, {})
    display_sync_summary(changes)


def test_display_sync_summary_with_changes():
    """Test displaying sync summary with various changes."""
    changes = SyncReport(
        new={"new.md"},
        modified={"mod.md"},
        deleted={"del.md"},
        moves={"old.md": "new.md"},
        checksums={"new.md": "abcd1234"}
    )
    display_sync_summary(changes)


def test_display_detailed_sync_results_no_changes():
    """Test displaying detailed results with no changes."""
    changes = SyncReport(set(), set(), set(), {}, {})
    display_detailed_sync_results(changes)


def test_display_detailed_sync_results_with_changes():
    """Test displaying detailed results with various changes."""
    changes = SyncReport(
        new={"new.md"},
        modified={"mod.md"},
        deleted={"del.md"},
        moves={"old.md": "new.md"},
        checksums={"new.md": "abcd1234", "mod.md": "efgh5678"}
    )
    display_detailed_sync_results(changes)


@pytest.mark.asyncio
async def test_run_sync_basic(sync_service, tmp_path, monkeypatch):
    """Test basic sync operation."""
    # Set up test environment
    monkeypatch.setenv("HOME", str(tmp_path))
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    
    # Create test files
    test_file = knowledge_dir / "test.md"
    test_file.write_text("""---
title: Test
---
# Test
Some content""")

    # Run sync - should detect new file
    await run_sync(verbose=True)


@pytest.mark.asyncio
async def test_run_sync_watch_mode(sync_service, tmp_path, monkeypatch):
    """Test sync with watch mode."""
    # Set up test environment
    monkeypatch.setenv("HOME", str(tmp_path))
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    
    # Start sync in watch mode but cancel after a short time
    with pytest.raises(asyncio.CancelledError):
        task = asyncio.create_task(run_sync(watch=True))
        await asyncio.sleep(0.1)  # Let it start
        task.cancel()
        await task


def test_sync_command():
    """Test the sync command."""
    result = runner.invoke(app, ["sync", "--verbose"])
    assert result.exit_code == 0
