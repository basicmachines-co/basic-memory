"""Tests for cloud sync and bisync command behavior."""

import importlib
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.config import ProjectEntry, ProjectMode
from basic_memory.schemas.cloud import WorkspaceInfo

runner = CliRunner()


@pytest.mark.parametrize(
    "argv",
    [
        ["cloud", "sync", "--name", "research"],
        ["cloud", "bisync", "--name", "research"],
    ],
)
def test_cloud_sync_commands_skip_explicit_cloud_project_sync(monkeypatch, argv, config_manager):
    """Cloud sync commands should not trigger an extra explicit cloud project sync."""
    project_sync_command = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    config = config_manager.load_config()
    config.set_project_mode("research", ProjectMode.CLOUD)
    config_manager.save_config(config)

    monkeypatch.setattr(project_sync_command, "_require_cloud_credentials", lambda _config: None)
    monkeypatch.setattr(
        project_sync_command, "_require_personal_workspace", lambda _name, _config: None
    )
    monkeypatch.setattr(
        project_sync_command,
        "get_mount_info",
        lambda: _async_value(SimpleNamespace(bucket_name="tenant-bucket")),
    )
    monkeypatch.setattr(
        project_sync_command,
        "_get_cloud_project",
        lambda _name: _async_value(
            SimpleNamespace(name="research", external_id="external-project-id", path="research")
        ),
    )
    monkeypatch.setattr(
        project_sync_command,
        "_get_sync_project",
        lambda _name, _config, _project_data: (SimpleNamespace(name="research"), "/tmp/research"),
    )
    monkeypatch.setattr(project_sync_command, "project_sync", lambda *args, **kwargs: True)
    monkeypatch.setattr(project_sync_command, "project_bisync", lambda *args, **kwargs: True)

    result = runner.invoke(app, argv)

    assert result.exit_code == 0, result.output
    assert "Database sync initiated" not in result.output


def test_cloud_bisync_fails_fast_when_sync_entry_disappears(monkeypatch, config_manager):
    """Bisync should raise a runtime error when validated sync config vanishes before persistence."""
    project_sync_command = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    config = config_manager.load_config()
    config.projects.pop("research", None)
    config_manager.save_config(config)

    monkeypatch.setattr(project_sync_command, "_require_cloud_credentials", lambda _config: None)
    monkeypatch.setattr(
        project_sync_command, "_require_personal_workspace", lambda _name, _config: None
    )
    monkeypatch.setattr(
        project_sync_command,
        "get_mount_info",
        lambda: _async_value(SimpleNamespace(bucket_name="tenant-bucket")),
    )
    monkeypatch.setattr(
        project_sync_command,
        "_get_cloud_project",
        lambda _name: _async_value(
            SimpleNamespace(name="research", external_id="external-project-id", path="research")
        ),
    )
    monkeypatch.setattr(
        project_sync_command,
        "_get_sync_project",
        lambda _name, _config, _project_data: (SimpleNamespace(name="research"), "/tmp/research"),
    )
    monkeypatch.setattr(project_sync_command, "project_bisync", lambda *args, **kwargs: True)

    result = runner.invoke(app, ["cloud", "bisync", "--name", "research"])

    assert result.exit_code == 1, result.output
    assert "unexpectedly missing after validation" in result.output


@pytest.mark.parametrize(
    "argv",
    [
        ["cloud", "sync", "--name", "research"],
        ["cloud", "bisync", "--name", "research"],
        ["cloud", "check", "--name", "research"],
        ["cloud", "bisync-reset", "research"],
        ["cloud", "sync-setup", "research", "/tmp/research"],
    ],
)
def test_cloud_sync_commands_block_organization_workspace(monkeypatch, argv, config_manager):
    """Rclone sync commands should fail before setup/execution for Team workspaces."""
    project_sync_command = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    config = config_manager.load_config()
    config.cloud_api_key = "bmc_test"
    config.projects["research"] = ProjectEntry(
        path="/tmp/research",
        mode=ProjectMode.CLOUD,
        workspace_id="team-tenant",
        local_sync_path="/tmp/research",
    )
    config_manager.save_config(config)

    monkeypatch.setattr(
        project_sync_command,
        "get_available_workspaces",
        lambda: _async_value([_workspace("team-tenant", "organization", "team")]),
    )
    monkeypatch.setattr(
        project_sync_command,
        "get_mount_info",
        lambda: pytest.fail("workspace guard should run before mount lookup"),
    )

    result = runner.invoke(app, argv)

    assert result.exit_code == 1, result.output
    assert "Local rclone sync/bisync is supported only for Personal workspaces" in result.output
    assert "Team workspaces are accessed through the cloud API/MCP" in result.output


def test_require_personal_workspace_allows_personal_workspace(monkeypatch, config_manager):
    """Personal workspaces keep the existing rclone sync path available."""
    project_sync_command = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    config = config_manager.load_config()
    config.projects["research"] = ProjectEntry(
        path="/tmp/research",
        mode=ProjectMode.CLOUD,
        workspace_id="personal-tenant",
        local_sync_path="/tmp/research",
    )
    config_manager.save_config(config)

    monkeypatch.setattr(
        project_sync_command,
        "get_available_workspaces",
        lambda: _async_value([_workspace("personal-tenant", "personal", "personal")]),
    )

    workspace = project_sync_command._require_personal_workspace("research", config)

    assert workspace.tenant_id == "personal-tenant"


def test_bisync_reset_skips_workspace_check_without_credentials(monkeypatch, tmp_path):
    """Resetting local bisync state stays harmless when no cloud credentials exist."""
    project_sync_command = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    monkeypatch.setattr(
        project_sync_command,
        "get_available_workspaces",
        lambda: pytest.fail("workspace lookup requires cloud credentials"),
    )
    monkeypatch.setattr(
        project_sync_command,
        "get_project_bisync_state",
        lambda _name: tmp_path / "missing-state",
    )

    result = runner.invoke(app, ["cloud", "bisync-reset", "research"])

    assert result.exit_code == 0, result.output
    assert "No bisync state found for project 'research'" in result.output


async def _async_value(value):
    return value


def _workspace(tenant_id: str, workspace_type: str, slug: str) -> WorkspaceInfo:
    return WorkspaceInfo(
        tenant_id=tenant_id,
        workspace_type=workspace_type,
        slug=slug,
        name=slug.title(),
        role="owner",
        is_default=False,
        has_active_subscription=True,
    )
