"""Tests for workspace CLI commands."""

import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

import basic_memory.config
from basic_memory.cli.app import app
from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.schemas.cloud import WorkspaceInfo

# Importing the cloud package registers workspace_app on cloud_app.
import basic_memory.cli.commands.cloud as cloud_cmd  # noqa: F401
import basic_memory.cli.commands.cloud.workspace as workspace_cmd  # noqa: F401


@pytest.fixture
def runner():
    return CliRunner()


def test_workspace_list_prints_available_workspaces(runner, monkeypatch):
    async def fake_get_available_workspaces(context=None):
        return [
            WorkspaceInfo(
                tenant_id="11111111-1111-1111-1111-111111111111",
                workspace_type="personal",
                name="Personal",
                role="owner",
            ),
            WorkspaceInfo(
                tenant_id="22222222-2222-2222-2222-222222222222",
                workspace_type="organization",
                name="Team",
                role="editor",
            ),
        ]

    monkeypatch.setattr(workspace_cmd, "get_available_workspaces", fake_get_available_workspaces)

    result = runner.invoke(app, ["cloud", "workspace", "list"])

    assert result.exit_code == 0
    assert "Available Workspaces" in result.stdout
    assert "Personal" in result.stdout
    assert "Team" in result.stdout
    # Tenant ID may be truncated by Rich table rendering
    assert "11111111" in result.stdout


def test_workspace_list_requires_oauth_login_message(runner, monkeypatch):
    async def fail_get_available_workspaces(context=None):  # pragma: no cover
        raise RuntimeError("Workspace discovery requires OAuth login. Run 'bm cloud login' first.")

    monkeypatch.setattr(workspace_cmd, "get_available_workspaces", fail_get_available_workspaces)

    result = runner.invoke(app, ["cloud", "workspace", "list"])

    assert result.exit_code == 1
    assert "Workspace discovery requires OAuth login" in result.stdout
    assert "bm cloud login" in result.stdout


class TestWorkspaceSetDefault:
    """Tests for 'bm cloud workspace set-default' command."""

    @pytest.fixture(autouse=True)
    def _setup_config(self, monkeypatch):
        """Set up a temp config for each test."""
        self.temp_dir = tempfile.mkdtemp()
        temp_path = Path(self.temp_dir)
        config_dir = temp_path / ".basic-memory"
        config_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HOME", str(temp_path))
        monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(config_dir))
        basic_memory.config._CONFIG_CACHE = None

        config_manager = ConfigManager()
        test_config = BasicMemoryConfig(
            projects={"main": {"path": str(temp_path / "main")}},
        )
        config_manager.save_config(test_config)

    def test_set_default_workspace_by_name(self, runner, monkeypatch):
        async def fake_get_available_workspaces(context=None):
            return [
                WorkspaceInfo(
                    tenant_id="11111111-1111-1111-1111-111111111111",
                    workspace_type="personal",
                    name="Personal",
                    role="owner",
                ),
            ]

        monkeypatch.setattr(
            workspace_cmd, "get_available_workspaces", fake_get_available_workspaces
        )

        result = runner.invoke(app, ["cloud", "workspace", "set-default", "Personal"])

        assert result.exit_code == 0
        assert "Default workspace set" in result.stdout
        assert "Personal" in result.stdout

        # Verify config was updated
        basic_memory.config._CONFIG_CACHE = None
        config = ConfigManager().config
        assert config.default_workspace == "11111111-1111-1111-1111-111111111111"

    def test_set_default_workspace_by_tenant_id(self, runner, monkeypatch):
        async def fake_get_available_workspaces(context=None):
            return [
                WorkspaceInfo(
                    tenant_id="22222222-2222-2222-2222-222222222222",
                    workspace_type="organization",
                    name="Team",
                    role="editor",
                ),
            ]

        monkeypatch.setattr(
            workspace_cmd, "get_available_workspaces", fake_get_available_workspaces
        )

        result = runner.invoke(
            app, ["cloud", "workspace", "set-default", "22222222-2222-2222-2222-222222222222"]
        )

        assert result.exit_code == 0
        assert "Default workspace set" in result.stdout

    def test_set_default_workspace_not_found(self, runner, monkeypatch):
        async def fake_get_available_workspaces(context=None):
            return [
                WorkspaceInfo(
                    tenant_id="11111111-1111-1111-1111-111111111111",
                    workspace_type="personal",
                    name="Personal",
                    role="owner",
                ),
            ]

        monkeypatch.setattr(
            workspace_cmd, "get_available_workspaces", fake_get_available_workspaces
        )

        result = runner.invoke(app, ["cloud", "workspace", "set-default", "Nonexistent"])

        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_set_default_workspace_no_workspaces(self, runner, monkeypatch):
        async def fake_get_available_workspaces(context=None):
            return []

        monkeypatch.setattr(
            workspace_cmd, "get_available_workspaces", fake_get_available_workspaces
        )

        result = runner.invoke(app, ["cloud", "workspace", "set-default", "Personal"])

        assert result.exit_code == 1
        assert "No accessible workspaces" in result.stdout

    def test_set_default_workspace_oauth_error(self, runner, monkeypatch):
        async def fail_get_available_workspaces(context=None):  # pragma: no cover
            raise RuntimeError(
                "Workspace discovery requires OAuth login. Run 'bm cloud login' first."
            )

        monkeypatch.setattr(
            workspace_cmd, "get_available_workspaces", fail_get_available_workspaces
        )

        result = runner.invoke(app, ["cloud", "workspace", "set-default", "Personal"])

        assert result.exit_code == 1
        assert "OAuth login" in result.stdout
