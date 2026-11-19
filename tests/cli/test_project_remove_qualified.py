"""`bm project remove <workspace>/<project>` deletes that workspace's project only.

A qualified name has no local config entry, so it used to be sent to the
default workspace. The server drops the workspace segment of a qualified name,
so the delete removed the same-named project in whichever workspace the request
reached, after a prompt that named a different one.

Here a local project ``main`` is in config, and cloud workspaces ``ws`` and
``team`` each hold a ``main``; ``team`` is the configured default workspace. The
real routing runs (``get_client`` and the workspace project index lookup); only
the transports and the delete request are stubbed, so a test can see where each
request went.
"""

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.mcp.clients.project import ProjectClient
from basic_memory.schemas.cloud import WorkspaceInfo
from basic_memory.schemas.project_info import ProjectItem

# Importing registers project subcommands on the shared app instance.
import basic_memory.cli.commands.project as project_cmd  # noqa: F401

WS_MAIN_ID = "11111111-1111-1111-1111-111111111111"
TEAM_MAIN_ID = "22222222-2222-2222-2222-222222222222"
LOCAL_MAIN_ID = "33333333-3333-3333-3333-333333333333"


def _flat(output: str) -> str:
    """Collapse whitespace so assertions survive Rich wrapping at the console width."""
    return " ".join(output.split())


def _workspace(tenant_id: str, slug: str, *, is_default: bool = False) -> WorkspaceInfo:
    return WorkspaceInfo(
        tenant_id=tenant_id,
        workspace_type="organization",
        slug=slug,
        name=slug.title(),
        role="owner",
        is_default=is_default,
    )


def _main(external_id: str) -> ProjectItem:
    return ProjectItem(id=1, external_id=external_id, name="main", path="/main", is_default=False)


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def local_main(tmp_path, monkeypatch) -> Path:
    """An isolated config: a local ``main`` with a note, default workspace ``team``."""
    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None

    main_dir = tmp_path / "main"
    main_dir.mkdir()
    (main_dir / "todo.md").write_text("# todo\n")

    config_dir = tmp_path / ".basic-memory"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "env": "dev",
                "projects": {"main": {"path": str(main_dir), "mode": "local"}},
                "default_project": "main",
                "default_workspace": "team-tenant",
                "cloud_api_key": "bmc_test123",
            },
            indent=2,
        )
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    return main_dir


@pytest.fixture
def routes(monkeypatch):
    """Record where each remove request goes and which project it deletes."""
    import basic_memory.mcp.async_client as async_client
    import basic_memory.mcp.project_context as project_context
    from basic_memory.mcp.workspace_project_index import (
        WorkspaceProjectEntry,
        build_workspace_project_index,
    )

    ws = _workspace("ws-tenant", "ws")
    team = _workspace("team-tenant", "team", is_default=True)
    index = build_workspace_project_index(
        (ws, team),
        (
            WorkspaceProjectEntry(workspace=ws, project=_main(WS_MAIN_ID)),
            WorkspaceProjectEntry(workspace=team, project=_main(TEAM_MAIN_ID)),
        ),
    )

    async def fake_index(context=None, force_refresh=False):
        return index

    routes_taken: list[str] = []
    seen: dict[str, object] = {"routes": routes_taken}

    @asynccontextmanager
    async def cloud_client(config, timeout, workspace=None):
        routes_taken.append(f"cloud:{workspace}")
        yield SimpleNamespace(route="cloud")

    @asynccontextmanager
    async def local_client(timeout):
        routes_taken.append("local")
        yield SimpleNamespace(route="local")

    async def fake_resolve_project(self, identifier):
        return SimpleNamespace(external_id=LOCAL_MAIN_ID)

    async def fake_delete_project(self, external_id, delete_notes=False):
        seen["deleted"] = external_id
        seen["delete_notes"] = delete_notes
        return SimpleNamespace(message="Project deleted", file_delete_status="complete")

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fake_index)
    monkeypatch.setattr(async_client, "_cloud_client", cloud_client)
    monkeypatch.setattr(async_client, "_asgi_client", local_client)
    monkeypatch.setattr(ProjectClient, "resolve_project", fake_resolve_project)
    monkeypatch.setattr(ProjectClient, "delete_project", fake_delete_project)
    return seen


@pytest.mark.parametrize("argument", ["ws/main", WS_MAIN_ID])
def test_qualified_remove_deletes_only_the_named_workspaces_project(
    runner, local_main, routes, argument
):
    result = runner.invoke(app, ["project", "remove", argument, "--yes"])

    assert result.exit_code == 0, result.stdout
    assert routes["routes"] == ["cloud:ws-tenant"], "no request may reach the default workspace"
    assert routes["deleted"] == WS_MAIN_ID
    assert routes["delete_notes"] is True
    assert (local_main / "todo.md").exists(), "the same-named local project is untouched"


def test_qualified_remove_prompt_names_the_workspace_and_project(runner, local_main, routes):
    result = runner.invoke(app, ["project", "remove", "ws/main"], input="n\n")

    assert result.exit_code == 0, result.stdout
    output = _flat(result.stdout)
    assert "Delete cloud project 'main' in workspace 'ws' and its cloud files?" in output
    assert "Remove cancelled - nothing deleted" in output
    assert "deleted" not in routes


def test_bare_name_remove_still_goes_to_the_local_project_and_keeps_its_files(
    runner, local_main, routes
):
    result = runner.invoke(app, ["project", "remove", "main"])

    assert result.exit_code == 0, result.stdout
    assert routes["routes"] == ["local"]
    assert routes["deleted"] == LOCAL_MAIN_ID
    assert routes["delete_notes"] is False
    assert (local_main / "todo.md").exists()


def test_unknown_workspace_deletes_nothing_and_says_not_found(runner, local_main, routes):
    result = runner.invoke(app, ["project", "remove", "nosuchws/main", "--yes"])

    assert result.exit_code == 1
    output = _flat(result.stdout)
    assert "cloud project 'nosuchws/main' was not found" in output
    assert "Nothing was deleted." in output
    assert routes["routes"] == []
    assert "deleted" not in routes


def test_unknown_project_in_a_known_workspace_deletes_nothing(runner, local_main, routes):
    result = runner.invoke(app, ["project", "remove", "ws/nosuchproject", "--yes"])

    assert result.exit_code == 1
    assert "was not found" in _flat(result.stdout)
    assert routes["routes"] == []
    assert "deleted" not in routes


def test_local_flag_skips_the_workspace_lookup(runner, local_main, routes, monkeypatch):
    """`--local` is a routing override: a qualified name goes to the local API as before."""
    import basic_memory.mcp.project_context as project_context

    async def fail_lookup(*args, **kwargs):  # pragma: no cover
        raise AssertionError("--local must not consult cloud workspaces")

    monkeypatch.setattr(project_context, "resolve_workspace_project_identifier", fail_lookup)

    result = runner.invoke(app, ["project", "remove", "ws/main", "--local"])

    assert result.exit_code == 0, result.stdout
    assert routes["routes"] == ["local"]
