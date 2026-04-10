"""Tests for cloud sync and bisync command behavior."""

import importlib
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.config import ProjectMode

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


async def _async_value(value):
    return value
