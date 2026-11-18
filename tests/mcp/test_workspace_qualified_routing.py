"""A workspace-qualified project or a cloud external id reaches that cloud project.

The setup has a local project ``main`` in config and a cloud project ``main`` in
workspace ``ws``. A team workspace also holds a ``main``. ``ws/main`` and the cloud
project's external id used to reach the local ``main`` without any error: the
client was opened with the bare name, and per-project routing (keyed by bare
name) sent it to the local app and dropped the workspace.

These tests run the real routing (``get_project_client`` and ``get_client``) and
the real API. Only the cloud transport is stubbed: it records the workspace the
request was sent to and is then served by the same test app, where the cloud
project is a second database row, ``cloud-main``, that is not in local config.
The two ``todo`` notes have different bodies, so a read shows which project
answered.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from fastmcp.exceptions import ToolError

from basic_memory import db
from basic_memory.config import ProjectEntry
from basic_memory.index.local_project import LocalProjectIndexRunner
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.schemas.cloud import WorkspaceInfo
from basic_memory.schemas.project_info import ProjectItem

LOCAL_BODY = "This is the local copy of the to-do list."
CLOUD_BODY = "This is the cloud copy of the to-do list."
TEAM_MAIN_ID = "99999999-9999-9999-9999-999999999999"


def _workspace(tenant_id: str, slug: str, *, is_default: bool = False) -> WorkspaceInfo:
    return WorkspaceInfo(
        tenant_id=tenant_id,
        workspace_type="organization",
        slug=slug,
        name=slug.title(),
        role="owner",
        is_default=is_default,
    )


def _write_todo(project_dir: Path, body: str) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "todo.md").write_text(
        f"---\ntitle: todo\ntype: note\npermalink: todo\n---\n\n# todo\n\n{body}\n"
    )


async def _create_indexed_project(session_maker, name: str, path: Path, body: str):
    _write_todo(path, body)
    async with db.scoped_session(session_maker) as session:
        project = await ProjectRepository().create(
            session,
            {"name": name, "path": str(path), "is_active": True, "is_default": False},
        )
    runner = LocalProjectIndexRunner(
        project_repository=ProjectRepository(), session_maker=session_maker
    )
    await runner.index_project(project.id, force_full=True)
    return project


@pytest_asyncio.fixture
async def same_name_setup(app, config_manager, engine_factory, tmp_path_factory, monkeypatch):
    """A local ``main`` in config, plus ``main`` in cloud workspaces ``ws`` and ``team``."""
    import basic_memory.mcp.async_client as async_client
    import basic_memory.mcp.project_context as project_context
    from basic_memory.mcp.workspace_project_index import (
        WorkspaceProjectEntry,
        build_workspace_project_index,
    )

    _, session_maker = engine_factory
    local_dir = tmp_path_factory.mktemp("local-main")
    cloud_dir = tmp_path_factory.mktemp("cloud-main")
    await _create_indexed_project(session_maker, "main", local_dir, LOCAL_BODY)
    cloud_row = await _create_indexed_project(session_maker, "cloud-main", cloud_dir, CLOUD_BODY)

    config = config_manager.load_config()
    config.projects["main"] = ProjectEntry(path=str(local_dir))
    config.cloud_api_key = "bmc_test123"
    config_manager.save_config(config)

    ws = _workspace("ws-tenant", "ws")
    team = _workspace("team-tenant", "team", is_default=True)
    cloud_main = ProjectItem(
        id=cloud_row.id,
        external_id=cloud_row.external_id,
        name="main",
        path="/main",
        is_default=False,
    )
    team_main = ProjectItem(
        id=999, external_id=TEAM_MAIN_ID, name="main", path="/main", is_default=False
    )
    index = build_workspace_project_index(
        (ws, team),
        (
            WorkspaceProjectEntry(workspace=ws, project=cloud_main),
            WorkspaceProjectEntry(workspace=team, project=team_main),
        ),
    )

    async def fake_index(context=None, force_refresh=False):
        return index

    cloud_workspaces: list[str | None] = []
    cloud_clients: list[object] = []
    serve_locally = async_client._asgi_client

    @asynccontextmanager
    async def recording_cloud_client(config, timeout, workspace=None):
        cloud_workspaces.append(workspace)
        async with serve_locally(timeout) as client:
            cloud_clients.append(client)
            yield client

    # The stand-in cloud project is a row in the one test database, so the local
    # app would find it by external id. On a real machine the local app does not
    # have the cloud project; answer that way on every route but the cloud one.
    validate_project = project_context.get_active_project

    async def local_app_lacks_the_cloud_project(client, project=None, context=None, headers=None):
        if project == cloud_row.external_id and client not in cloud_clients:
            raise ToolError(f"Project not found: '{project}'")
        return await validate_project(client, project, context, headers)

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fake_index)
    monkeypatch.setattr(project_context, "get_active_project", local_app_lacks_the_cloud_project)
    monkeypatch.setattr(async_client, "_cloud_client", recording_cloud_client)

    return {"cloud_id": cloud_row.external_id, "cloud_workspaces": cloud_workspaces}


@pytest.mark.asyncio
async def test_qualified_name_reaches_the_cloud_project_not_the_same_named_local_one(
    same_name_setup,
):
    from basic_memory.mcp.project_context import get_project_client

    async with get_project_client(project="ws/main") as (_client, active_project):
        assert active_project.external_id == same_name_setup["cloud_id"]

    assert same_name_setup["cloud_workspaces"] == ["ws-tenant"]


@pytest.mark.asyncio
async def test_cloud_external_id_as_project_reaches_the_cloud_project(same_name_setup):
    from basic_memory.mcp.project_context import get_project_client

    cloud_id = same_name_setup["cloud_id"]
    async with get_project_client(project=cloud_id) as (_client, active_project):
        assert active_project.external_id == cloud_id

    assert same_name_setup["cloud_workspaces"] == ["ws-tenant"]


@pytest.mark.asyncio
async def test_cloud_external_id_as_project_id_reaches_the_cloud_project(same_name_setup):
    from basic_memory.mcp.project_context import get_project_client

    cloud_id = same_name_setup["cloud_id"]
    async with get_project_client(project_id=cloud_id) as (_client, active_project):
        assert active_project.external_id == cloud_id

    # The local app is asked first and does not know the id; the cloud workspace does.
    assert same_name_setup["cloud_workspaces"] == ["ws-tenant"]


@pytest.mark.asyncio
async def test_bare_name_still_reaches_the_local_project(same_name_setup):
    from basic_memory.mcp.project_context import get_project_client

    async with get_project_client(project="main") as (_client, active_project):
        assert active_project.name == "main"
        assert active_project.external_id != same_name_setup["cloud_id"]

    assert same_name_setup["cloud_workspaces"] == []


@pytest.mark.asyncio
async def test_a_session_cached_local_project_is_not_reused_for_the_cloud_route(
    same_name_setup,
):
    """A bare-name call caches the local ``main``; ``ws/main`` must not reuse it."""
    from basic_memory.mcp.project_context import get_project_client
    from tests.mcp.conftest import ContextState, ctx

    context = ctx(ContextState())
    async with get_project_client(project="main", context=context) as (_client, local_project):
        assert local_project.external_id != same_name_setup["cloud_id"]

    async with get_project_client(project="ws/main", context=context) as (_client, active):
        assert active.external_id == same_name_setup["cloud_id"]

    assert same_name_setup["cloud_workspaces"] == ["ws-tenant"]


@pytest.mark.asyncio
@pytest.mark.parametrize("use_external_id", [False, True])
async def test_bm_read_with_a_qualified_project_reads_the_cloud_note(
    same_name_setup, use_external_id
):
    from basic_memory.mcp.tools import bm_read

    project = same_name_setup["cloud_id"] if use_external_id else "ws/main"
    content = await bm_read("todo", project=project)

    assert isinstance(content, str)
    assert CLOUD_BODY in content
    assert LOCAL_BODY not in content
    assert same_name_setup["cloud_workspaces"] == ["ws-tenant"]


@pytest.mark.asyncio
async def test_bm_read_with_the_bare_name_reads_the_local_note(same_name_setup):
    from basic_memory.mcp.tools import bm_read

    content = await bm_read("todo", project="main")

    assert isinstance(content, str)
    assert LOCAL_BODY in content
    assert same_name_setup["cloud_workspaces"] == []


@pytest.mark.asyncio
async def test_qualified_name_without_cloud_credentials_fails_instead_of_reading_local(
    same_name_setup, config_manager, monkeypatch
):
    """With no way to reach the cloud, the call fails; it never falls back to local."""
    import basic_memory.mcp.async_client as async_client
    from basic_memory.mcp.project_context import get_project_client

    monkeypatch.setattr(async_client, "has_cloud_credentials", lambda _config: False)

    with pytest.raises(RuntimeError, match="no cloud credentials"):
        async with get_project_client(project="ws/main"):
            pass  # pragma: no cover
