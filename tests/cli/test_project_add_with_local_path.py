"""Tests for bm project add with --local-path flag."""

import json
from pathlib import Path
from contextlib import asynccontextmanager

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.mcp.clients.project import ProjectClient
from basic_memory.schemas.project_info import ProjectStatusResponse

# Importing registers project subcommands on the shared app instance.
import basic_memory.cli.commands.project as project_cmd  # noqa: F401


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_config(tmp_path, monkeypatch):
    """Create a mock config with cloud credentials using environment variables."""
    # Invalidate config cache to ensure clean state for each test
    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None

    config_dir = tmp_path / ".basic-memory"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.json"

    config_data = {
        "env": "dev",
        "projects": {},
        "default_project": "main",
        "cloud_api_key": "bmc_test_key_123",
    }

    config_file.write_text(json.dumps(config_data, indent=2))

    # Set HOME to tmp_path so ConfigManager uses our test config
    monkeypatch.setenv("HOME", str(tmp_path))

    yield config_file


@pytest.fixture
def mock_api_client(monkeypatch):
    """Stub the API client for project add without stdlib mocks."""
    seen_workspaces: list[str | None] = []

    @asynccontextmanager
    async def fake_get_client(*, workspace=None):
        seen_workspaces.append(workspace)
        yield object()

    _response_data = {
        "message": "Project 'test-project' added successfully",
        "status": "success",
        "default": False,
        "old_project": None,
        "new_project": {
            "id": 1,
            "external_id": "12345678-1234-1234-1234-123456789012",
            "name": "test-project",
            "path": "/test-project",
            "is_default": False,
        },
    }

    calls: list[dict] = []

    async def fake_create_project(self, project_data):
        calls.append(project_data)
        return ProjectStatusResponse.model_validate(_response_data)

    monkeypatch.setattr(project_cmd, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "create_project", fake_create_project)

    return {"calls": calls, "workspaces": seen_workspaces}


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
            "--cloud",
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

    # Verify config was updated — sync path stored on the project entry
    config_data = json.loads(mock_config.read_text())
    assert "test-project" in config_data["projects"]
    entry = config_data["projects"]["test-project"]
    # Use as_posix() for cross-platform compatibility (Windows uses backslashes)
    assert entry["mode"] == "cloud"
    assert entry["local_sync_path"] == local_sync_dir.as_posix()
    assert entry.get("last_sync") is None
    assert entry.get("bisync_initialized", False) is False

    # Verify local directory was created
    assert local_sync_dir.exists()
    assert local_sync_dir.is_dir()


def test_project_add_without_local_path_no_config_entry(runner, mock_config, mock_api_client):
    """Test that bm project add without --local-path doesn't save to config."""
    result = runner.invoke(
        app,
        ["project", "add", "test-project", "--cloud"],
    )

    assert result.exit_code == 0
    assert "Project 'test-project' added successfully" in result.stdout
    assert "Local sync path configured" not in result.stdout

    # Verify config was NOT updated with cloud sync path
    config_data = json.loads(mock_config.read_text())
    # Project may or may not be in config, but if it is, local_sync_path should be null
    entry = config_data.get("projects", {}).get("test-project")
    if entry:
        assert entry.get("local_sync_path") is None


def test_project_add_local_path_expands_tilde(runner, mock_config, mock_api_client):
    """Test that --local-path ~/path expands to absolute path."""
    result = runner.invoke(
        app,
        ["project", "add", "test-project", "--cloud", "--local-path", "~/test-sync"],
    )

    assert result.exit_code == 0

    # Verify config has expanded path
    config_data = json.loads(mock_config.read_text())
    local_path = config_data["projects"]["test-project"]["local_sync_path"]
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
        ["project", "add", "test-project", "--cloud", "--local-path", str(nested_path)],
    )

    assert result.exit_code == 0
    assert nested_path.exists()
    assert nested_path.is_dir()


def test_project_add_cloud_visibility_passes_payload(runner, mock_config, mock_api_client):
    """Cloud project creation should forward visibility to the API payload."""
    result = runner.invoke(
        app,
        ["project", "add", "test-project", "--cloud", "--visibility", "shared"],
    )

    assert result.exit_code == 0
    assert mock_api_client["workspaces"] == [None]
    assert mock_api_client["calls"] == [
        {
            "name": "test-project",
            "path": "test-project",
            "local_sync_path": None,
            "set_default": False,
            "visibility": "shared",
        }
    ]


def test_project_add_cloud_workspace_resolves_and_persists(
    runner, mock_config, mock_api_client, monkeypatch, tmp_path
):
    """Cloud project add should resolve workspace names to tenant IDs."""
    from basic_memory.schemas.cloud import WorkspaceInfo

    local_sync_dir = tmp_path / "sync" / "team-notes"

    async def fake_get_available_workspaces():
        return [
            WorkspaceInfo(
                tenant_id="11111111-1111-1111-1111-111111111111",
                workspace_type="organization",
                name="Basic Memory",
                role="owner",
            ),
        ]

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fake_get_available_workspaces,
    )

    result = runner.invoke(
        app,
        [
            "project",
            "add",
            "team-notes",
            "--cloud",
            "--workspace",
            "Basic Memory",
            "--local-path",
            str(local_sync_dir),
        ],
    )

    assert result.exit_code == 0
    assert mock_api_client["workspaces"] == ["11111111-1111-1111-1111-111111111111"]
    assert mock_api_client["calls"] == [
        {
            "name": "team-notes",
            "path": "team-notes",
            "local_sync_path": local_sync_dir.as_posix(),
            "set_default": False,
            "visibility": "workspace",
        }
    ]

    config_data = json.loads(mock_config.read_text())
    entry = config_data["projects"]["team-notes"]
    assert entry["mode"] == "cloud"
    assert entry["workspace_id"] == "11111111-1111-1111-1111-111111111111"


def test_project_add_cloud_workspace_persists_without_local_path(
    runner, mock_config, mock_api_client, monkeypatch
):
    """Cloud project add should persist workspace routing even without local sync."""
    from basic_memory.schemas.cloud import WorkspaceInfo

    async def fake_get_available_workspaces():
        return [
            WorkspaceInfo(
                tenant_id="11111111-1111-1111-1111-111111111111",
                workspace_type="organization",
                name="Basic Memory",
                role="owner",
            ),
        ]

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fake_get_available_workspaces,
    )

    result = runner.invoke(
        app,
        [
            "project",
            "add",
            "team-notes",
            "--cloud",
            "--workspace",
            "Basic Memory",
        ],
    )

    assert result.exit_code == 0
    assert mock_api_client["workspaces"] == ["11111111-1111-1111-1111-111111111111"]
    assert mock_api_client["calls"] == [
        {
            "name": "team-notes",
            "path": "team-notes",
            "local_sync_path": None,
            "set_default": False,
            "visibility": "workspace",
        }
    ]

    config_data = json.loads(mock_config.read_text())
    entry = config_data["projects"]["team-notes"]
    assert entry["path"] == ""
    assert entry["mode"] == "cloud"
    assert entry["workspace_id"] == "11111111-1111-1111-1111-111111111111"
    assert entry["local_sync_path"] is None


def test_project_add_visibility_requires_cloud_mode(runner, mock_config, tmp_path):
    """Visibility is a cloud-only option."""
    project_path = tmp_path / "local-project"

    result = runner.invoke(
        app,
        [
            "project",
            "add",
            "local-project",
            str(project_path),
            "--visibility",
            "shared",
        ],
    )

    assert result.exit_code == 1
    assert "--visibility is only supported in cloud mode" in result.stdout


def test_project_add_rejects_invalid_visibility(runner, mock_config):
    """Invalid visibility values should fail fast before the API call."""
    result = runner.invoke(
        app,
        ["project", "add", "test-project", "--cloud", "--visibility", "team-only"],
    )

    assert result.exit_code == 1
    assert "Invalid visibility" in result.stdout
