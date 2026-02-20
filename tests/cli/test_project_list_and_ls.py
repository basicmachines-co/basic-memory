"""Tests for project list display and project ls routing behavior."""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app

# Importing registers project subcommands on the shared app instance.
import basic_memory.cli.commands.project as project_cmd  # noqa: F401


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def write_config(tmp_path, monkeypatch):
    """Write config.json under a temporary HOME and return the file path."""

    def _write(config_data: dict) -> Path:
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None

        config_dir = tmp_path / ".basic-memory"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps(config_data, indent=2))
        monkeypatch.setenv("HOME", str(tmp_path))
        return config_file

    return _write


@pytest.fixture
def mock_client(monkeypatch):
    """Mock get_client with a no-op async context manager."""

    @asynccontextmanager
    async def fake_get_client():
        yield object()

    monkeypatch.setattr(project_cmd, "get_client", fake_get_client)


def test_project_list_shows_local_cloud_presence_and_routes(
    runner: CliRunner, write_config, mock_client, tmp_path, monkeypatch
):
    """project list should show local/cloud paths plus CLI and MCP route targets."""
    alpha_local = (tmp_path / "alpha-local").as_posix()
    beta_local_sync = (tmp_path / "beta-sync").as_posix()

    write_config(
        {
            "env": "dev",
            "projects": {
                "alpha": {"path": alpha_local, "mode": "local"},
                "beta": {
                    "path": beta_local_sync,
                    "mode": "cloud",
                    "cloud_sync_path": beta_local_sync,
                },
            },
            "default_project": "alpha",
            "cloud_api_key": "bmc_test_key_123",
        }
    )

    local_payload = {
        "projects": [
            {
                "id": 1,
                "external_id": "11111111-1111-1111-1111-111111111111",
                "name": "alpha",
                "path": alpha_local,
                "is_default": True,
            }
        ],
        "default_project": "alpha",
    }

    cloud_payload = {
        "projects": [
            {
                "id": 2,
                "external_id": "22222222-2222-2222-2222-222222222222",
                "name": "alpha",
                "path": "/alpha",
                "is_default": True,
            },
            {
                "id": 3,
                "external_id": "33333333-3333-3333-3333-333333333333",
                "name": "beta",
                "path": "/beta",
                "is_default": False,
            },
        ],
        "default_project": "alpha",
    }

    class _Resp:
        def __init__(self, payload: dict):
            self._payload = payload

        def json(self):
            return self._payload

    async def fake_call_get(client, path: str, **kwargs):
        assert path == "/v2/projects/"
        if os.getenv("BASIC_MEMORY_FORCE_CLOUD", "").lower() in ("true", "1", "yes"):
            return _Resp(cloud_payload)
        return _Resp(local_payload)

    monkeypatch.setattr(project_cmd, "call_get", fake_call_get)

    result = runner.invoke(app, ["project", "list"], env={"COLUMNS": "240"})

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.stdout}"
    assert "Local Path" in result.stdout
    assert "Cloud Path" in result.stdout
    assert "CLI Route" in result.stdout
    assert "MCP (stdio)" in result.stdout

    lines = result.stdout.splitlines()
    alpha_line = next(line for line in lines if "│ alpha" in line)
    beta_line = next(line for line in lines if "│ beta" in line)

    assert "local" in alpha_line  # CLI route for alpha
    assert "cloud" in beta_line  # CLI route for beta
    assert "n/a" in beta_line  # MCP stdio route is unavailable for cloud-only projects
    assert "alpha-local" in result.stdout
    assert "/alpha" in result.stdout
    assert "/beta" in result.stdout


def test_project_ls_defaults_to_local_route(
    runner: CliRunner, write_config, mock_client, tmp_path, monkeypatch
):
    """project ls without flags should list local files and not require cloud credentials."""
    project_dir = tmp_path / "alpha-files"
    (project_dir / "docs").mkdir(parents=True, exist_ok=True)
    (project_dir / "notes.md").write_text("# local note")
    (project_dir / "docs" / "spec.md").write_text("# spec")

    write_config(
        {
            "env": "dev",
            "projects": {"alpha": {"path": project_dir.as_posix(), "mode": "cloud"}},
            "default_project": "alpha",
        }
    )

    payload = {
        "projects": [
            {
                "id": 1,
                "external_id": "11111111-1111-1111-1111-111111111111",
                "name": "alpha",
                "path": project_dir.as_posix(),
                "is_default": True,
            }
        ],
        "default_project": "alpha",
    }

    class _Resp:
        def json(self):
            return payload

    async def fake_call_get(client, path: str, **kwargs):
        assert path == "/v2/projects/"
        assert os.getenv("BASIC_MEMORY_FORCE_CLOUD", "").lower() not in ("true", "1", "yes")
        return _Resp()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("project_ls should not be used for default local route")

    monkeypatch.setattr(project_cmd, "call_get", fake_call_get)
    monkeypatch.setattr(project_cmd, "project_ls", fail_if_called)

    result = runner.invoke(app, ["project", "ls", "--name", "alpha"], env={"COLUMNS": "200"})

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.stdout}"
    assert "Files in alpha (LOCAL)" in result.stdout
    assert "notes.md" in result.stdout
    assert "docs/spec.md" in result.stdout


def test_project_ls_cloud_route_uses_cloud_listing(
    runner: CliRunner, write_config, mock_client, tmp_path, monkeypatch
):
    """project ls --cloud should fetch cloud project listing and print cloud-target heading."""
    write_config(
        {
            "env": "dev",
            "projects": {"alpha": {"path": str(tmp_path / "alpha"), "mode": "local"}},
            "default_project": "alpha",
            "cloud_api_key": "bmc_test_key_123",
        }
    )

    cloud_payload = {
        "projects": [
            {
                "id": 1,
                "external_id": "11111111-1111-1111-1111-111111111111",
                "name": "alpha",
                "path": "/alpha",
                "is_default": True,
            }
        ],
        "default_project": "alpha",
    }

    class _Resp:
        def json(self):
            return cloud_payload

    class _TenantInfo:
        bucket_name = "tenant-bucket"

    async def fake_call_get(client, path: str, **kwargs):
        assert path == "/v2/projects/"
        assert os.getenv("BASIC_MEMORY_FORCE_CLOUD", "").lower() in ("true", "1", "yes")
        return _Resp()

    async def fake_get_mount_info():
        return _TenantInfo()

    monkeypatch.setattr(project_cmd, "call_get", fake_call_get)
    monkeypatch.setattr(project_cmd, "get_mount_info", fake_get_mount_info)
    monkeypatch.setattr(project_cmd, "project_ls", lambda *args, **kwargs: ["        42 cloud.md"])

    result = runner.invoke(app, ["project", "ls", "--name", "alpha", "--cloud"])

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.stdout}"
    assert "Files in alpha (CLOUD)" in result.stdout
    assert "cloud.md" in result.stdout
