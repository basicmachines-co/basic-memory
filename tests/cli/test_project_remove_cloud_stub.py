"""`bm project remove` must retire the local routing entry of a cloud project (#1340).

`project add --cloud` writes a cloud-mode config entry (path "", workspace id) so
later commands route to the cloud. The cloud delete never touched local config,
so that stub outlived the project: list-projects kept showing it, a second
`remove` routed to the cloud and got "not found", and `add` refused the name.
"""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.mcp.clients.project import ProjectClient

# Importing registers project subcommands on the shared app instance.
import basic_memory.cli.commands.project as project_cmd  # noqa: F401


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """An isolated config with a local default and a cloud-only project entry."""
    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None

    config_dir = tmp_path / ".basic-memory"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "config.json"
    path.write_text(
        json.dumps(
            {
                "env": "dev",
                "projects": {
                    "main": {"path": str(tmp_path / "main"), "mode": "local"},
                    "openclaw-demo": {"path": "", "mode": "cloud", "workspace_id": "team-drew"},
                },
                "default_project": "main",
            },
            indent=2,
        )
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    return path


@pytest.fixture
def cloud_delete(monkeypatch):
    """Stub the API client so the cloud resolves and deletes the project."""
    seen: dict[str, str | None] = {}

    @asynccontextmanager
    async def fake_get_client(*, project_name=None, workspace=None):
        seen["workspace"] = workspace
        yield object()

    async def fake_resolve_project(self, identifier):
        return SimpleNamespace(external_id="ext-123")

    async def fake_delete_project(self, external_id, delete_notes=False):
        seen["deleted"] = external_id
        return SimpleNamespace(message="Project 'openclaw-demo' deletion queued")

    monkeypatch.setattr(project_cmd, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "resolve_project", fake_resolve_project)
    monkeypatch.setattr(ProjectClient, "delete_project", fake_delete_project)
    return seen


def test_removing_a_cloud_project_drops_its_local_routing_entry(runner, config_file, cloud_delete):
    result = runner.invoke(app, ["project", "remove", "openclaw-demo"])

    assert result.exit_code == 0, result.stdout
    assert cloud_delete == {"workspace": "team-drew", "deleted": "ext-123"}
    projects = json.loads(config_file.read_text())["projects"]
    assert "openclaw-demo" not in projects
    assert "main" in projects, "unrelated entries must survive"
