"""Test project-scoped rclone commands."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from basic_memory.cli.commands.cloud.rclone_commands import (
    RcloneError,
    SyncProject,
    bisync_initialized,
    get_bmignore_filter_path,
    get_project_bisync_state,
    get_project_remote,
    project_bisync,
    project_check,
    project_ls,
    project_sync,
)


def test_sync_project_dataclass():
    """Test SyncProject dataclass."""
    project = SyncProject(
        name="research",
        path="app/data/research",
        local_sync_path="/Users/test/research",
    )

    assert project.name == "research"
    assert project.path == "app/data/research"
    assert project.local_sync_path == "/Users/test/research"


def test_sync_project_optional_local_path():
    """Test SyncProject with optional local_sync_path."""
    project = SyncProject(
        name="research",
        path="app/data/research",
    )

    assert project.name == "research"
    assert project.path == "app/data/research"
    assert project.local_sync_path is None


def test_get_project_remote():
    """Test building rclone remote path."""
    project = SyncProject(name="research", path="app/data/research")

    remote = get_project_remote(project, "my-bucket")

    assert remote == "basic-memory-cloud:my-bucket/app/data/research"


def test_get_project_remote_strips_leading_slash():
    """Test that leading slash is stripped from cloud path."""
    project = SyncProject(name="research", path="/app/data/research")

    remote = get_project_remote(project, "my-bucket")

    assert remote == "basic-memory-cloud:my-bucket/app/data/research"


def test_get_project_bisync_state():
    """Test getting bisync state directory path."""
    state_path = get_project_bisync_state("research")

    expected = Path.home() / ".basic-memory" / "bisync-state" / "research"
    assert state_path == expected


def test_bisync_initialized_false_when_not_exists(tmp_path, monkeypatch):
    """Test bisync_initialized returns False when state doesn't exist."""
    # Patch to use tmp directory
    monkeypatch.setattr(
        "basic_memory.cli.commands.cloud.rclone_commands.get_project_bisync_state",
        lambda project_name: tmp_path / project_name,
    )

    assert bisync_initialized("research") is False


def test_bisync_initialized_false_when_empty(tmp_path, monkeypatch):
    """Test bisync_initialized returns False when state directory is empty."""
    state_dir = tmp_path / "research"
    state_dir.mkdir()

    monkeypatch.setattr(
        "basic_memory.cli.commands.cloud.rclone_commands.get_project_bisync_state",
        lambda project_name: tmp_path / project_name,
    )

    assert bisync_initialized("research") is False


def test_bisync_initialized_true_when_has_files(tmp_path, monkeypatch):
    """Test bisync_initialized returns True when state has files."""
    state_dir = tmp_path / "research"
    state_dir.mkdir()
    (state_dir / "state.lst").touch()

    monkeypatch.setattr(
        "basic_memory.cli.commands.cloud.rclone_commands.get_project_bisync_state",
        lambda project_name: tmp_path / project_name,
    )

    assert bisync_initialized("research") is True


@patch("basic_memory.cli.commands.cloud.rclone_commands.subprocess.run")
def test_project_sync_success(mock_run):
    """Test successful project sync."""
    mock_run.return_value = MagicMock(returncode=0)

    project = SyncProject(
        name="research",
        path="app/data/research",
        local_sync_path="/tmp/research",
    )

    result = project_sync(project, "my-bucket", dry_run=True)

    assert result is True
    mock_run.assert_called_once()

    # Check command arguments
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "rclone"
    assert cmd[1] == "sync"
    assert cmd[2] == "/tmp/research"
    assert cmd[3] == "basic-memory-cloud:my-bucket/app/data/research"
    assert "--dry-run" in cmd


@patch("basic_memory.cli.commands.cloud.rclone_commands.subprocess.run")
def test_project_sync_with_verbose(mock_run):
    """Test project sync with verbose flag."""
    mock_run.return_value = MagicMock(returncode=0)

    project = SyncProject(
        name="research",
        path="app/data/research",
        local_sync_path="/tmp/research",
    )

    project_sync(project, "my-bucket", verbose=True)

    cmd = mock_run.call_args[0][0]
    assert "--verbose" in cmd
    assert "--progress" not in cmd


@patch("basic_memory.cli.commands.cloud.rclone_commands.subprocess.run")
def test_project_sync_with_progress(mock_run):
    """Test project sync with progress (default)."""
    mock_run.return_value = MagicMock(returncode=0)

    project = SyncProject(
        name="research",
        path="app/data/research",
        local_sync_path="/tmp/research",
    )

    project_sync(project, "my-bucket")

    cmd = mock_run.call_args[0][0]
    assert "--progress" in cmd
    assert "--verbose" not in cmd


def test_project_sync_no_local_path():
    """Test project sync raises error when local_sync_path not configured."""
    project = SyncProject(name="research", path="app/data/research")

    with pytest.raises(RcloneError) as exc_info:
        project_sync(project, "my-bucket")

    assert "no local_sync_path configured" in str(exc_info.value)


@patch("basic_memory.cli.commands.cloud.rclone_commands.subprocess.run")
@patch("basic_memory.cli.commands.cloud.rclone_commands.bisync_initialized")
def test_project_bisync_success(mock_bisync_init, mock_run):
    """Test successful project bisync."""
    mock_bisync_init.return_value = True  # Already initialized
    mock_run.return_value = MagicMock(returncode=0)

    project = SyncProject(
        name="research",
        path="app/data/research",
        local_sync_path="/tmp/research",
    )

    result = project_bisync(project, "my-bucket")

    assert result is True
    mock_run.assert_called_once()

    # Check command arguments
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "rclone"
    assert cmd[1] == "bisync"
    assert "--conflict-resolve=newer" in cmd
    assert "--max-delete=25" in cmd
    assert "--resilient" in cmd


@patch("basic_memory.cli.commands.cloud.rclone_commands.subprocess.run")
@patch("basic_memory.cli.commands.cloud.rclone_commands.bisync_initialized")
def test_project_bisync_requires_resync_first_time(mock_bisync_init, mock_run):
    """Test that first bisync requires --resync flag."""
    mock_bisync_init.return_value = False  # Not initialized

    project = SyncProject(
        name="research",
        path="app/data/research",
        local_sync_path="/tmp/research",
    )

    with pytest.raises(RcloneError) as exc_info:
        project_bisync(project, "my-bucket")

    assert "requires --resync" in str(exc_info.value)
    mock_run.assert_not_called()


@patch("basic_memory.cli.commands.cloud.rclone_commands.subprocess.run")
@patch("basic_memory.cli.commands.cloud.rclone_commands.bisync_initialized")
def test_project_bisync_with_resync_flag(mock_bisync_init, mock_run):
    """Test bisync with --resync flag for first time."""
    mock_bisync_init.return_value = False  # Not initialized
    mock_run.return_value = MagicMock(returncode=0)

    project = SyncProject(
        name="research",
        path="app/data/research",
        local_sync_path="/tmp/research",
    )

    result = project_bisync(project, "my-bucket", resync=True)

    assert result is True
    cmd = mock_run.call_args[0][0]
    assert "--resync" in cmd


@patch("basic_memory.cli.commands.cloud.rclone_commands.subprocess.run")
@patch("basic_memory.cli.commands.cloud.rclone_commands.bisync_initialized")
def test_project_bisync_dry_run_skips_init_check(mock_bisync_init, mock_run):
    """Test that dry-run skips initialization check."""
    mock_bisync_init.return_value = False  # Not initialized
    mock_run.return_value = MagicMock(returncode=0)

    project = SyncProject(
        name="research",
        path="app/data/research",
        local_sync_path="/tmp/research",
    )

    # Should not raise error even though not initialized
    result = project_bisync(project, "my-bucket", dry_run=True)

    assert result is True
    cmd = mock_run.call_args[0][0]
    assert "--dry-run" in cmd


def test_project_bisync_no_local_path():
    """Test project bisync raises error when local_sync_path not configured."""
    project = SyncProject(name="research", path="app/data/research")

    with pytest.raises(RcloneError) as exc_info:
        project_bisync(project, "my-bucket")

    assert "no local_sync_path configured" in str(exc_info.value)


@patch("basic_memory.cli.commands.cloud.rclone_commands.subprocess.run")
def test_project_check_success(mock_run):
    """Test successful project check."""
    mock_run.return_value = MagicMock(returncode=0)

    project = SyncProject(
        name="research",
        path="app/data/research",
        local_sync_path="/tmp/research",
    )

    result = project_check(project, "my-bucket")

    assert result is True
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "rclone"
    assert cmd[1] == "check"


@patch("basic_memory.cli.commands.cloud.rclone_commands.subprocess.run")
def test_project_check_with_one_way(mock_run):
    """Test project check with one-way flag."""
    mock_run.return_value = MagicMock(returncode=0)

    project = SyncProject(
        name="research",
        path="app/data/research",
        local_sync_path="/tmp/research",
    )

    project_check(project, "my-bucket", one_way=True)

    cmd = mock_run.call_args[0][0]
    assert "--one-way" in cmd


def test_project_check_no_local_path():
    """Test project check raises error when local_sync_path not configured."""
    project = SyncProject(name="research", path="app/data/research")

    with pytest.raises(RcloneError) as exc_info:
        project_check(project, "my-bucket")

    assert "no local_sync_path configured" in str(exc_info.value)


@patch("basic_memory.cli.commands.cloud.rclone_commands.subprocess.run")
def test_project_ls_success(mock_run):
    """Test successful project ls."""
    mock_run.return_value = MagicMock(
        returncode=0, stdout="file1.md\nfile2.md\nsubdir/file3.md\n"
    )

    project = SyncProject(name="research", path="app/data/research")

    files = project_ls(project, "my-bucket")

    assert len(files) == 3
    assert "file1.md" in files
    assert "file2.md" in files
    assert "subdir/file3.md" in files


@patch("basic_memory.cli.commands.cloud.rclone_commands.subprocess.run")
def test_project_ls_with_subpath(mock_run):
    """Test project ls with subdirectory."""
    mock_run.return_value = MagicMock(returncode=0, stdout="")

    project = SyncProject(name="research", path="app/data/research")

    project_ls(project, "my-bucket", path="subdir")

    cmd = mock_run.call_args[0][0]
    assert cmd[-1] == "basic-memory-cloud:my-bucket/app/data/research/subdir"