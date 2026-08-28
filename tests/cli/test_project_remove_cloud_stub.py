"""`bm project remove` must retire the local routing entry of a cloud project (#1340).

`project add --cloud` writes a cloud-mode config entry (path "", workspace id) so
later commands route to the cloud. The cloud delete never touched local config,
so that stub outlived the project: list-projects kept showing it, a second
`remove` routed to the cloud and got "not found", and `add` refused the name.
"""

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.cli.commands.cloud import rclone_commands
from basic_memory.mcp.clients.project import ProjectClient

# Importing registers project subcommands on the shared app instance.
import basic_memory.cli.commands.project as project_cmd  # noqa: F401


@pytest.fixture
def runner():
    return CliRunner()


def _write_config(tmp_path: Path, monkeypatch, projects: dict[str, dict[str, object]]) -> Path:
    """Write an isolated config with the given project entries and point HOME at it."""
    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None

    config_dir = tmp_path / ".basic-memory"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "config.json"
    path.write_text(
        json.dumps({"env": "dev", "projects": projects, "default_project": "main"}, indent=2)
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    return path


def _projects(config_file: Path) -> dict[str, dict[str, object]]:
    return json.loads(config_file.read_text())["projects"]


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """A local default plus a cloud-only routing entry."""
    return _write_config(
        tmp_path,
        monkeypatch,
        {
            "main": {"path": str(tmp_path / "main"), "mode": "local"},
            "openclaw-demo": {"path": "", "mode": "cloud", "workspace_id": "team-drew"},
        },
    )


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
        return SimpleNamespace(message="Project deletion queued")

    monkeypatch.setattr(project_cmd, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "resolve_project", fake_resolve_project)
    monkeypatch.setattr(ProjectClient, "delete_project", fake_delete_project)
    return seen


def test_removing_a_cloud_project_drops_its_local_routing_entry(runner, config_file, cloud_delete):
    result = runner.invoke(app, ["project", "remove", "openclaw-demo"])

    assert result.exit_code == 0, result.stdout
    assert cloud_delete == {"workspace": "team-drew", "deleted": "ext-123"}
    projects = _projects(config_file)
    assert "openclaw-demo" not in projects
    assert "main" in projects, "unrelated entries must survive"


def test_permalink_form_of_the_name_still_finds_the_entry(
    tmp_path, monkeypatch, runner, cloud_delete
):
    """The API resolves `my-research` for `My Research`; the config lookup must too."""
    config_file = _write_config(
        tmp_path,
        monkeypatch,
        {
            "main": {"path": str(tmp_path / "main"), "mode": "local"},
            "My Research": {"path": "", "mode": "cloud", "workspace_id": "team-drew"},
        },
    )

    result = runner.invoke(app, ["project", "remove", "my-research"])

    assert result.exit_code == 0, result.stdout
    assert "My Research" not in _projects(config_file)


def test_cloud_flag_is_only_a_routing_override_for_a_local_entry(
    tmp_path, monkeypatch, runner, cloud_delete
):
    """`remove --cloud` on a same-named local project deletes the cloud copy, not local config."""
    config_file = _write_config(
        tmp_path,
        monkeypatch,
        {
            "main": {"path": str(tmp_path / "main"), "mode": "local"},
            "research": {"path": str(tmp_path / "research"), "mode": "local"},
        },
    )

    result = runner.invoke(app, ["project", "remove", "research", "--cloud"])

    assert result.exit_code == 0, result.stdout
    assert cloud_delete["deleted"] == "ext-123"
    assert "research" in _projects(config_file), "the local project keeps its entry"


def test_auto_routed_cloud_delete_cleans_local_sync_artifacts(
    tmp_path, monkeypatch, runner, cloud_delete
):
    """Cleanup follows the route the delete takes, not the raw --cloud flag."""
    local_sync = tmp_path / "research-sync"
    local_sync.mkdir()
    config_file = _write_config(
        tmp_path,
        monkeypatch,
        {
            "main": {"path": str(tmp_path / "main"), "mode": "local"},
            "research": {
                "path": str(local_sync),
                "mode": "cloud",
                "workspace_id": "team-drew",
                "local_sync_path": str(local_sync),
                "bisync_initialized": True,
            },
        },
    )
    bisync_state = tmp_path / "bisync-state" / "research"
    bisync_state.mkdir(parents=True)
    monkeypatch.setattr(
        rclone_commands, "get_project_bisync_state", lambda project_name: bisync_state
    )

    result = runner.invoke(app, ["project", "remove", "research"])

    assert result.exit_code == 0, result.stdout
    assert not bisync_state.exists(), "stale bisync state would let a recreated name skip --resync"
    assert local_sync.exists(), "notes stay on disk without --delete-notes"
    # Rich wraps the long temp path across lines, so match the message alone.
    assert "Local files remain at" in result.stdout
    assert "research" not in _projects(config_file)


def test_bisync_state_cleanup_uses_the_canonical_entry_name(
    tmp_path, monkeypatch, runner, cloud_delete
):
    """Removing `My Research` as `my-research` must clear `bisync-state/My Research`."""
    local_sync = tmp_path / "research-sync"
    local_sync.mkdir()
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "main": {"path": str(tmp_path / "main"), "mode": "local"},
            "My Research": {
                "path": str(local_sync),
                "mode": "cloud",
                "workspace_id": "team-drew",
                "local_sync_path": str(local_sync),
                "bisync_initialized": True,
            },
        },
    )
    states = {"My Research": tmp_path / "bisync-state" / "My Research"}
    states["My Research"].mkdir(parents=True)
    monkeypatch.setattr(
        rclone_commands,
        "get_project_bisync_state",
        lambda project_name: states.get(project_name, tmp_path / "bisync-state" / project_name),
    )

    result = runner.invoke(app, ["project", "remove", "my-research"])

    assert result.exit_code == 0, result.stdout
    assert not states["My Research"].exists()


def test_explicit_local_route_leaves_config_removal_to_the_local_service(
    tmp_path, monkeypatch, runner, cloud_delete
):
    """`remove --local` on a cloud-mode entry with a local row: the local API owns the entry."""
    config_file = _write_config(
        tmp_path,
        monkeypatch,
        {
            "main": {"path": str(tmp_path / "main"), "mode": "local"},
            "research": {"path": "", "mode": "cloud", "workspace_id": "team-drew"},
        },
    )

    result = runner.invoke(app, ["project", "remove", "research", "--local"])

    assert result.exit_code == 0, result.stdout
    # The stubbed API did not touch config; the CLI must not double-delete either.
    assert "research" in _projects(config_file)
