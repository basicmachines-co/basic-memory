"""Tests for cloud sync and bisync command behavior."""

import importlib
from contextlib import asynccontextmanager
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
def test_cloud_sync_commands_use_incremental_db_sync(monkeypatch, argv, config_manager):
    """Cloud sync commands should not force a full database re-index after file sync."""
    project_sync_command = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

    seen: dict[str, object] = {}
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

    @asynccontextmanager
    async def fake_get_client(*, project_name=None, workspace=None):
        seen["project_name"] = project_name
        seen["workspace"] = workspace
        yield object()

    class FakeProjectClient:
        def __init__(self, _client):
            pass

        async def sync(self, external_id: str, force_full: bool = False):
            seen["external_id"] = external_id
            seen["force_full"] = force_full
            return {"message": "queued"}

    monkeypatch.setattr(project_sync_command, "get_client", fake_get_client)
    monkeypatch.setattr(project_sync_command, "ProjectClient", FakeProjectClient)

    result = runner.invoke(app, argv)

    assert result.exit_code == 0, result.output
    assert seen["project_name"] == "research"
    assert seen["external_id"] == "external-project-id"
    assert seen["force_full"] is False


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
