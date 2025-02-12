"""Tests for CLI status command."""

import os
from pathlib import Path
import pytest
import pytest_asyncio
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.cli.commands.status import (
    add_files_to_tree,
    build_directory_summary,
    group_changes_by_directory,
    run_status,
    display_changes,
    get_file_change_scanner,
)
from basic_memory.sync.utils import SyncReport
from basic_memory.sync import FileChangeScanner
from basic_memory.repository import EntityRepository

# Set up CLI runner
runner = CliRunner()


@pytest_asyncio.fixture
async def file_change_scanner(session_maker):
    """Create FileChangeScanner instance with test database."""
    entity_repository = EntityRepository(session_maker)
    scanner = FileChangeScanner(entity_repository)
    return scanner


@pytest.mark.asyncio
async def test_run_status_no_changes(file_change_scanner, tmp_path, monkeypatch):
    """Test status command with no changes."""
    # Set up test environment
    monkeypatch.setenv("HOME", str(tmp_path))
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()

    # Run status check
    await run_status(file_change_scanner, verbose=False)


@pytest.mark.asyncio
async def test_run_status_with_changes(file_change_scanner, tmp_path, monkeypatch):
    """Test status command with actual file changes."""
    # Set up test environment
    monkeypatch.setenv("HOME", str(tmp_path))
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()

    # Create test files
    test_file = knowledge_dir / "test.md"
    test_file.write_text("# Test\nSome content")

    # Run status check - should detect new file
    await run_status(file_change_scanner, verbose=True)


@pytest.mark.asyncio
async def test_status_command_error(tmp_path, monkeypatch):
    """Test CLI status command error handling."""
    # Set up invalid environment
    nonexistent = tmp_path / "nonexistent"
    monkeypatch.setenv("HOME", str(nonexistent))
    monkeypatch.setenv("DATABASE_PATH", str(nonexistent / "nonexistent.db"))

    # Should exit with code 1 when error occurs
    result = runner.invoke(app, ["status", "--verbose"])
    assert result.exit_code == 1


def test_display_changes_no_changes():
    """Test displaying no changes."""
    changes = SyncReport(set(), set(), set(), {}, {})
    display_changes("Test", changes, verbose=True)
    display_changes("Test", changes, verbose=False)


def test_display_changes_with_changes():
    """Test displaying various changes."""
    changes = SyncReport(
        new={"dir1/new.md"},
        modified={"dir1/mod.md"},
        deleted={"dir2/del.md"},
        moves={"old.md": "new.md"},
        checksums={"dir1/new.md": "abcd1234"},
    )
    display_changes("Test", changes, verbose=True)
    display_changes("Test", changes, verbose=False)


def test_build_directory_summary():
    """Test building directory change summary."""
    counts = {
        "new": 2,
        "modified": 1,
        "moved": 1,
        "deleted": 1,
    }
    summary = build_directory_summary(counts)
    assert "+2" in summary
    assert "~1" in summary
    assert "↔1" in summary
    assert "-1" in summary


def test_build_directory_summary_empty():
    """Test summary with no changes."""
    counts = {
        "new": 0,
        "modified": 0,
        "moved": 0,
        "deleted": 0,
    }
    summary = build_directory_summary(counts)
    assert summary == ""


def test_group_changes_by_directory():
    """Test grouping changes by directory."""
    changes = SyncReport(
        new={"dir1/new.md", "dir2/new2.md"},
        modified={"dir1/mod.md"},
        deleted={"dir2/del.md"},
        moves={"dir1/old.md": "dir2/new.md"},
        checksums={},
    )

    grouped = group_changes_by_directory(changes)

    assert grouped["dir1"]["new"] == 1
    assert grouped["dir1"]["modified"] == 1
    assert grouped["dir1"]["moved"] == 1

    assert grouped["dir2"]["new"] == 1
    assert grouped["dir2"]["deleted"] == 1
    assert grouped["dir2"]["moved"] == 1


def test_add_files_to_tree():
    """Test adding files to tree visualization."""
    from rich.tree import Tree

    # Test with various path patterns
    paths = {
        "dir1/file1.md",  # Normal nested file
        "dir1/file2.md",  # Another in same dir
        "dir2/subdir/file3.md",  # Deeper nesting
        "root.md",  # Root level file
    }

    # Test without checksums
    tree = Tree("Test")
    add_files_to_tree(tree, paths, "green")

    # Test with checksums
    checksums = {"dir1/file1.md": "abcd1234", "dir1/file2.md": "efgh5678"}

    tree = Tree("Test with checksums")
    add_files_to_tree(tree, paths, "green", checksums)
