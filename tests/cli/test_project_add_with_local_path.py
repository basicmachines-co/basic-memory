"""Tests for bm project add with --local-path flag."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_config(tmp_path, monkeypatch):
    """Create a mock config in cloud mode using environment variables."""
    # Invalidate config cache to ensure clean state for each test
    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None

    config_dir = tmp_path / ".basic-memory"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.json"

    config_data = {
        "env": "dev",
        "projects": {},
        "default_project": "main",
        "cloud_mode": True,
        "cloud_projects": {},
    }

    config_file.write_text(json.dumps(config_data, indent=2))

    # Set HOME to tmp_path so ConfigManager uses our test config
    monkeypatch.setenv("HOME", str(tmp_path))

    yield config_file


@pytest.fixture
def mock_api_client():
    """Mock the API client for project add."""
    with patch("basic_memory.cli.commands.project.get_client"):
        # Mock call_post to return a proper response
        mock_response = AsyncMock()
        mock_response.json = lambda: {
            "message": "Project 'test-project' added successfully",
            "status": "success",
            "default": False,
            "old_project": None,
            "new_project": {
                "id": 1,
                "name": "test-project",
                "path": "/test-project",
                "is_default": False,
            },
        }

        with patch(
            "basic_memory.cli.commands.project.call_post", return_value=mock_response
        ) as mock_post:
            yield mock_post


def test_project_add_with_local_path_saves_to_config(
    runner, mock_config, mock_api_client, tmp_path
):
    """Test that bm project add --local-path saves sync path to config."""
    local_sync_dir = tmp_path / "sync" / "test-project"

    result = runner.invoke(
        app,
        [
            "project",
            "add",
            "test-project",
            "--local-path",
            str(local_sync_dir),
        ],
    )

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, Stdout: {result.stdout}"
    assert "Project 'test-project' added successfully" in result.stdout
    assert "Local sync path configured" in result.stdout
    # Check path is present (may be line-wrapped in output)
    assert "test-project" in result.stdout
    assert "sync" in result.stdout

    # Verify config was updated
    config_data = json.loads(mock_config.read_text())
    assert "test-project" in config_data["cloud_projects"]
    # Use as_posix() for cross-platform compatibility (Windows uses backslashes)
    assert config_data["cloud_projects"]["test-project"]["local_path"] == local_sync_dir.as_posix()
    assert config_data["cloud_projects"]["test-project"]["last_sync"] is None
    assert config_data["cloud_projects"]["test-project"]["bisync_initialized"] is False

    # Verify local directory was created
    assert local_sync_dir.exists()
    assert local_sync_dir.is_dir()


def test_project_add_without_local_path_no_config_entry(runner, mock_config, mock_api_client):
    """Test that bm project add without --local-path doesn't save to config."""
    result = runner.invoke(
        app,
        ["project", "add", "test-project"],
    )

    assert result.exit_code == 0
    assert "Project 'test-project' added successfully" in result.stdout
    assert "Local sync path configured" not in result.stdout

    # Verify config was NOT updated with cloud_projects entry
    config_data = json.loads(mock_config.read_text())
    assert "test-project" not in config_data.get("cloud_projects", {})


def test_project_add_local_path_expands_tilde(runner, mock_config, mock_api_client):
    """Test that --local-path ~/path expands to absolute path."""
    result = runner.invoke(
        app,
        ["project", "add", "test-project", "--local-path", "~/test-sync"],
    )

    assert result.exit_code == 0

    # Verify config has expanded path
    config_data = json.loads(mock_config.read_text())
    local_path = config_data["cloud_projects"]["test-project"]["local_path"]
    # Path should be absolute (starts with / on Unix or drive letter on Windows)
    assert Path(local_path).is_absolute()
    assert "~" not in local_path
    assert local_path.endswith("/test-sync")


def test_project_add_local_path_creates_nested_directories(
    runner, mock_config, mock_api_client, tmp_path
):
    """Test that --local-path creates nested directories."""
    nested_path = tmp_path / "a" / "b" / "c" / "test-project"

    result = runner.invoke(
        app,
        ["project", "add", "test-project", "--local-path", str(nested_path)],
    )

    assert result.exit_code == 0
    assert nested_path.exists()
    assert nested_path.is_dir()
