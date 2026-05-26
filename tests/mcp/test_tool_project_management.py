"""Tests for MCP project management tools."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select

from basic_memory import db
from basic_memory.mcp.tools import list_memory_projects, create_memory_project, delete_project
from basic_memory.config import BasicMemoryConfig, ProjectEntry
from basic_memory.mcp.tools.project_management import _merge_projects, _merge_workspace_projects
from basic_memory.models.project import Project
from basic_memory.schemas.project_info import ProjectItem, ProjectList


# --- Helpers ---


def _make_project(
    name: str,
    path: str,
    *,
    id: int = 1,
    external_id: str = "00000000-0000-0000-0000-000000000001",
    is_default: bool = False,
    display_name: str | None = None,
    is_private: bool = False,
) -> ProjectItem:
    return ProjectItem(
        id=id,
        external_id=external_id,
        name=name,
        path=path,
        is_default=is_default,
        display_name=display_name,
        is_private=is_private,
    )


def _make_list(projects: list[ProjectItem], default: str | None = None) -> ProjectList:
    return ProjectList(projects=projects, default_project=default)


# --- Existing tests (updated for source labels) ---


@pytest.mark.asyncio
async def test_list_memory_projects_unconstrained(app, test_project):
    result = await list_memory_projects()
    assert "Available projects:" in result
    assert f"- {test_project.name}" in result


@pytest.mark.asyncio
async def test_list_memory_projects_shows_display_name(app, client, test_project):
    """When a project has display_name set, list_memory_projects shows 'display_name (name)' format."""
    mock_project = _make_project(
        "private-fb83af23",
        "/tmp/private",
        id=1,
        display_name="My Notes",
        is_private=True,
    )
    regular_project = _make_project(
        "main",
        "/tmp/main",
        id=2,
        external_id="00000000-0000-0000-0000-000000000002",
        is_default=True,
    )
    mock_list = _make_list([regular_project, mock_project], default="main")

    with patch(
        "basic_memory.mcp.clients.project.ProjectClient.list_projects",
        new_callable=AsyncMock,
        return_value=mock_list,
    ):
        result = await list_memory_projects()

    # Regular project shows name with source label
    assert "- main (local)" in result
    # Private project shows display_name with slug in parentheses, then source
    assert "- My Notes (private-fb83af23) (local)" in result


@pytest.mark.asyncio
async def test_list_memory_projects_no_display_name_shows_name_only(app, client, test_project):
    """When a project has no display_name, list_memory_projects shows just the name."""
    project = _make_project("my-project", "/tmp/my-project", is_default=True)
    mock_list = _make_list([project], default="my-project")

    with patch(
        "basic_memory.mcp.clients.project.ProjectClient.list_projects",
        new_callable=AsyncMock,
        return_value=mock_list,
    ):
        result = await list_memory_projects()

    assert "- my-project (local)" in result


@pytest.mark.asyncio
async def test_list_memory_projects_constrained_env(monkeypatch, app, test_project):
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", test_project.name)
    result = await list_memory_projects()
    assert f"Project: {test_project.name}" in result
    assert "constrained to a single project" in result


@pytest.mark.asyncio
async def test_create_and_delete_project_and_name_match_branch(
    app, tmp_path_factory, session_maker
):
    # Create a project through the tool (exercises POST + response formatting).
    project_root = tmp_path_factory.mktemp("extra-project-home")
    result = await create_memory_project(
        project_name="My Project",
        project_path=str(project_root),
        set_default=False,
    )
    assert isinstance(result, str)
    assert result.startswith("✓")
    assert "My Project" in result

    # Make permalink intentionally not derived from name so delete_project hits the name-match branch.
    async with db.scoped_session(session_maker) as session:
        project = (
            await session.execute(select(Project).where(Project.name == "My Project"))
        ).scalar_one()
        project.permalink = "custom-permalink"
        await session.commit()

    delete_result = await delete_project("My Project")
    assert delete_result.startswith("✓")


@pytest.mark.asyncio
async def test_create_memory_project_resolves_workspace_slug(app, tmp_path_factory):
    """A friendly workspace slug resolves to the tenant id used for cloud routing."""
    from basic_memory.mcp.clients import ProjectClient
    from basic_memory.schemas.project_info import ProjectStatusResponse

    project_root = tmp_path_factory.mktemp("ws-project-home")
    captured: dict[str, str | None] = {}

    @asynccontextmanager
    async def fake_get_client(*, workspace=None, project_name=None):
        captured["workspace"] = workspace
        async with httpx.AsyncClient(base_url="http://testserver") as client:
            yield client

    fake_status = ProjectStatusResponse(
        message="Project created",
        status="success",
        default=False,
        new_project=_make_project("WS Project", str(project_root)),
    )

    with (
        patch(
            "basic_memory.mcp.tools.project_management.get_client",
            new=fake_get_client,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.is_factory_mode",
            return_value=True,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.resolve_workspace_parameter",
            new_callable=AsyncMock,
            return_value=_make_workspace(
                "tenant-abc-123",
                "Team Paul",
                workspace_type="organization",
                slug="team-paul",
            ),
        ) as mock_resolve_workspace,
        patch.object(
            ProjectClient,
            "list_projects",
            new_callable=AsyncMock,
            return_value=_make_list([], default=None),
        ),
        patch.object(
            ProjectClient,
            "create_project",
            new_callable=AsyncMock,
            return_value=fake_status,
        ),
        patch(
            "basic_memory.mcp.project_context.invalidate_workspace_project_index",
            new_callable=AsyncMock,
        ),
    ):
        await create_memory_project(
            project_name="WS Project",
            project_path=str(project_root),
            workspace="team-paul",
        )

    mock_resolve_workspace.assert_awaited_once_with(workspace="team-paul", context=None)
    assert captured["workspace"] == "tenant-abc-123"


@pytest.mark.asyncio
async def test_create_memory_project_workspace_is_local_noop(app, tmp_path_factory):
    """Local create accepts workspace without requiring cloud workspace discovery."""
    from basic_memory.mcp.clients import ProjectClient
    from basic_memory.schemas.project_info import ProjectStatusResponse

    project_root = tmp_path_factory.mktemp("local-ws-project-home")
    captured: dict[str, str | None] = {}

    @asynccontextmanager
    async def fake_get_client(*, workspace=None, project_name=None):
        captured["workspace"] = workspace
        async with httpx.AsyncClient(base_url="http://testserver") as client:
            yield client

    fake_status = ProjectStatusResponse(
        message="Project created",
        status="success",
        default=False,
        new_project=_make_project("Local WS Project", str(project_root)),
    )

    with (
        patch(
            "basic_memory.mcp.tools.project_management.get_client",
            new=fake_get_client,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.is_factory_mode",
            return_value=False,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.has_cloud_credentials",
            return_value=False,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.resolve_workspace_parameter",
            new_callable=AsyncMock,
        ) as mock_resolve_workspace,
        patch.object(
            ProjectClient,
            "list_projects",
            new_callable=AsyncMock,
            return_value=_make_list([], default=None),
        ),
        patch.object(
            ProjectClient,
            "create_project",
            new_callable=AsyncMock,
            return_value=fake_status,
        ),
        patch(
            "basic_memory.mcp.project_context.invalidate_workspace_project_index",
            new_callable=AsyncMock,
        ),
    ):
        await create_memory_project(
            project_name="Local WS Project",
            project_path=str(project_root),
            workspace="team-paul",
        )

    mock_resolve_workspace.assert_not_awaited()
    assert captured["workspace"] == "team-paul"


@pytest.mark.asyncio
async def test_create_memory_project_default_workspace_is_none(app, tmp_path_factory):
    """When workspace is omitted, get_client receives workspace=None (default workspace)."""
    from basic_memory.mcp.clients import ProjectClient
    from basic_memory.schemas.project_info import ProjectStatusResponse

    project_root = tmp_path_factory.mktemp("default-ws-project-home")
    captured: dict[str, str | None] = {"workspace": "sentinel"}

    @asynccontextmanager
    async def fake_get_client(*, workspace=None, project_name=None):
        captured["workspace"] = workspace
        async with httpx.AsyncClient(base_url="http://testserver") as client:
            yield client

    fake_status = ProjectStatusResponse(
        message="Project created",
        status="success",
        default=False,
        new_project=_make_project("Default WS Project", str(project_root)),
    )

    with (
        patch(
            "basic_memory.mcp.tools.project_management.get_client",
            new=fake_get_client,
        ),
        patch.object(
            ProjectClient,
            "list_projects",
            new_callable=AsyncMock,
            return_value=_make_list([], default=None),
        ),
        patch.object(
            ProjectClient,
            "create_project",
            new_callable=AsyncMock,
            return_value=fake_status,
        ),
        patch(
            "basic_memory.mcp.project_context.invalidate_workspace_project_index",
            new_callable=AsyncMock,
        ),
    ):
        await create_memory_project(
            project_name="Default WS Project",
            project_path=str(project_root),
        )

    assert captured["workspace"] is None


@pytest.mark.asyncio
async def test_create_memory_project_constrained_with_workspace_returns_disabled_message(
    monkeypatch, tmp_path_factory
):
    """A constrained MCP session rejects creation before resolving a workspace selector."""
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "locked-project")
    project_root = tmp_path_factory.mktemp("constrained-create-project-home")

    with (
        patch(
            "basic_memory.mcp.tools.project_management.is_factory_mode",
            return_value=True,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.resolve_workspace_parameter",
            new_callable=AsyncMock,
            side_effect=RuntimeError("bad workspace"),
        ) as mock_resolve_workspace,
    ):
        result = await create_memory_project(
            project_name="Any Project",
            project_path=str(project_root),
            workspace="missing-team",
        )

    mock_resolve_workspace.assert_not_awaited()
    assert "Project creation disabled" in result
    assert "locked-project" in result


@pytest.mark.asyncio
async def test_delete_project_resolves_workspace_slug(app):
    """A friendly workspace slug resolves to the tenant id used for delete routing."""
    from basic_memory.mcp.clients import ProjectClient
    from basic_memory.schemas.project_info import ProjectStatusResponse

    target_project = _make_project(
        "WS Project",
        "/ws-project",
        external_id="project-uuid",
    )
    captured: dict[str, str | None] = {}

    @asynccontextmanager
    async def fake_get_client(*, workspace=None, project_name=None):
        captured["workspace"] = workspace
        async with httpx.AsyncClient(base_url="http://testserver") as client:
            yield client

    fake_status = ProjectStatusResponse(
        message="Project deleted",
        status="success",
        default=False,
        old_project=target_project,
    )

    with (
        patch(
            "basic_memory.mcp.tools.project_management.get_client",
            new=fake_get_client,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.is_factory_mode",
            return_value=True,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.resolve_workspace_parameter",
            new_callable=AsyncMock,
            return_value=_make_workspace(
                "tenant-abc-123",
                "Team Paul",
                workspace_type="organization",
                slug="team-paul",
            ),
        ) as mock_resolve_workspace,
        patch.object(
            ProjectClient,
            "list_projects",
            new_callable=AsyncMock,
            return_value=_make_list([target_project], default=None),
        ),
        patch.object(
            ProjectClient,
            "delete_project",
            new_callable=AsyncMock,
            return_value=fake_status,
        ) as mock_delete_project,
        patch(
            "basic_memory.mcp.project_context.invalidate_workspace_project_index",
            new_callable=AsyncMock,
        ),
    ):
        result = await delete_project("WS Project", workspace="team-paul")

    mock_resolve_workspace.assert_awaited_once_with(workspace="team-paul", context=None)
    assert captured["workspace"] == "tenant-abc-123"
    mock_delete_project.assert_awaited_once_with("project-uuid")
    assert result.startswith("✓")


@pytest.mark.asyncio
async def test_delete_project_workspace_is_local_noop(app):
    """Local delete accepts workspace without requiring cloud workspace discovery."""
    from basic_memory.mcp.clients import ProjectClient
    from basic_memory.schemas.project_info import ProjectStatusResponse

    target_project = _make_project(
        "Local WS Project",
        "/local-ws-project",
        external_id="local-project-uuid",
    )
    captured: dict[str, str | None] = {}

    @asynccontextmanager
    async def fake_get_client(*, workspace=None, project_name=None):
        captured["workspace"] = workspace
        async with httpx.AsyncClient(base_url="http://testserver") as client:
            yield client

    fake_status = ProjectStatusResponse(
        message="Project deleted",
        status="success",
        default=False,
        old_project=target_project,
    )

    with (
        patch(
            "basic_memory.mcp.tools.project_management.get_client",
            new=fake_get_client,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.is_factory_mode",
            return_value=False,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.has_cloud_credentials",
            return_value=False,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.resolve_workspace_parameter",
            new_callable=AsyncMock,
        ) as mock_resolve_workspace,
        patch.object(
            ProjectClient,
            "list_projects",
            new_callable=AsyncMock,
            return_value=_make_list([target_project], default=None),
        ),
        patch.object(
            ProjectClient,
            "delete_project",
            new_callable=AsyncMock,
            return_value=fake_status,
        ),
        patch(
            "basic_memory.mcp.project_context.invalidate_workspace_project_index",
            new_callable=AsyncMock,
        ),
    ):
        await delete_project("Local WS Project", workspace="team-paul")

    mock_resolve_workspace.assert_not_awaited()
    assert captured["workspace"] == "team-paul"


@pytest.mark.asyncio
async def test_delete_project_default_workspace_is_none(app):
    """When workspace is omitted, delete_project routes through the default workspace."""
    from basic_memory.mcp.clients import ProjectClient
    from basic_memory.schemas.project_info import ProjectStatusResponse

    target_project = _make_project(
        "Default WS Project",
        "/default-ws-project",
        external_id="default-project-uuid",
    )
    captured: dict[str, str | None] = {"workspace": "sentinel"}

    @asynccontextmanager
    async def fake_get_client(*, workspace=None, project_name=None):
        captured["workspace"] = workspace
        async with httpx.AsyncClient(base_url="http://testserver") as client:
            yield client

    fake_status = ProjectStatusResponse(
        message="Project deleted",
        status="success",
        default=False,
        old_project=target_project,
    )

    with (
        patch(
            "basic_memory.mcp.tools.project_management.get_client",
            new=fake_get_client,
        ),
        patch.object(
            ProjectClient,
            "list_projects",
            new_callable=AsyncMock,
            return_value=_make_list([target_project], default=None),
        ),
        patch.object(
            ProjectClient,
            "delete_project",
            new_callable=AsyncMock,
            return_value=fake_status,
        ),
        patch(
            "basic_memory.mcp.project_context.invalidate_workspace_project_index",
            new_callable=AsyncMock,
        ),
    ):
        await delete_project("Default WS Project")

    assert captured["workspace"] is None


@pytest.mark.asyncio
async def test_delete_project_constrained_with_workspace_returns_disabled_message(monkeypatch):
    """A constrained MCP session rejects deletion before resolving a workspace selector."""
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "locked-project")

    with (
        patch(
            "basic_memory.mcp.tools.project_management.is_factory_mode",
            return_value=True,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.resolve_workspace_parameter",
            new_callable=AsyncMock,
            side_effect=RuntimeError("bad workspace"),
        ) as mock_resolve_workspace,
    ):
        result = await delete_project("Any Project", workspace="missing-team")

    mock_resolve_workspace.assert_not_awaited()
    assert "Project deletion disabled" in result
    assert "locked-project" in result


# --- Cloud merge tests ---


@pytest.mark.asyncio
async def test_list_memory_projects_local_and_cloud_merge(app, test_project):
    """When cloud credentials exist, projects from both sources are merged by permalink."""
    local_main = _make_project("main", "/home/user/basic-memory", is_default=True)
    local_specs = _make_project(
        "specs", "/home/user/specs", id=2, external_id="00000000-0000-0000-0000-000000000002"
    )
    local_list = _make_list([local_main, local_specs], default="main")

    cloud_main = _make_project("main", "/main", id=10, external_id="cloud-main-uuid")
    cloud_llc = _make_project(
        "basic-memory-llc", "/basic-memory-llc", id=11, external_id="cloud-llc-uuid"
    )
    workspace = _make_workspace(
        "personal-tenant",
        "Personal",
        slug="personal",
        is_default=True,
    )
    workspace_index = _make_workspace_index([(workspace, [cloud_main, cloud_llc])])

    with (
        patch(
            "basic_memory.mcp.clients.project.ProjectClient.list_projects",
            new_callable=AsyncMock,
            return_value=local_list,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.has_cloud_credentials",
            return_value=True,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.ensure_workspace_project_index",
            new_callable=AsyncMock,
            return_value=workspace_index,
        ),
    ):
        result = await list_memory_projects()

    # Both local+cloud project shows merged source
    assert "- main (local+cloud)" in result
    # Local-only project
    assert "- specs (local)" in result
    # Cloud-only project
    assert "- basic-memory-llc (cloud)" in result


@pytest.mark.asyncio
async def test_list_memory_projects_no_cloud_credentials(app, test_project):
    """When no cloud credentials exist, only local projects are shown."""
    with patch(
        "basic_memory.mcp.tools.project_management.has_cloud_credentials",
        return_value=False,
    ):
        result = await list_memory_projects()

    assert "Available projects:" in result
    assert f"- {test_project.name} (local)" in result
    # No cloud source labels
    assert "cloud)" not in result


@pytest.mark.asyncio
async def test_list_memory_projects_cloud_failure_graceful(app, test_project):
    """When cloud fetch fails, local projects are still returned."""
    with (
        patch(
            "basic_memory.mcp.tools.project_management.has_cloud_credentials",
            return_value=True,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.ensure_workspace_project_index",
            new_callable=AsyncMock,
            side_effect=RuntimeError("cloud unavailable"),
        ),
    ):
        result = await list_memory_projects()

    assert "Available projects:" in result
    assert f"- {test_project.name} (local)" in result


@pytest.mark.asyncio
async def test_list_memory_projects_factory_mode(app, test_project):
    """Factory mode lists projects from every accessible workspace."""
    personal_project = _make_project(
        "personal-main",
        "/personal-main",
        is_default=True,
        external_id="personal-project-uuid",
    )
    team_project = _make_project(
        "team-specs",
        "/team-specs",
        id=2,
        external_id="team-project-uuid",
    )
    personal_ws = _make_workspace(
        "personal-tenant",
        "Personal",
        slug="personal",
        is_default=True,
    )
    team_ws = _make_workspace(
        "team-tenant",
        "Team Paul",
        "organization",
        slug="team-paul",
    )
    workspace_index = _make_workspace_index(
        [
            (personal_ws, [personal_project]),
            (team_ws, [team_project]),
        ]
    )

    with (
        patch(
            "basic_memory.mcp.tools.project_management.is_factory_mode",
            return_value=True,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.ensure_workspace_project_index",
            new_callable=AsyncMock,
            return_value=workspace_index,
        ) as mock_index,
    ):
        result = await list_memory_projects()

    mock_index.assert_awaited_once()
    assert "Workspace: Personal (personal default)" in result
    assert "Workspace: Team Paul (team-paul)" in result
    assert "- personal-main (cloud) [personal-project-uuid]" in result
    assert "- team-specs (cloud) [team-project-uuid]" in result


@pytest.mark.asyncio
async def test_list_memory_projects_factory_mode_json_includes_workspace(app, test_project):
    """In factory mode, JSON output includes workspace metadata for all cloud projects."""
    default_project = _make_project(
        "personal-main",
        "/personal-main",
        is_default=True,
        external_id="personal-project-uuid",
    )
    org_project = _make_project(
        "cloud-proj",
        "/cloud-proj",
        id=2,
        external_id="org-project-uuid",
    )
    personal_ws = _make_workspace(
        "personal-tenant",
        "Personal",
        slug="personal",
        is_default=True,
    )
    org_ws = _make_workspace("tenant-abc", "My Org", "organization")
    workspace_index = _make_workspace_index(
        [
            (personal_ws, [default_project]),
            (org_ws, [org_project]),
        ]
    )

    with (
        patch(
            "basic_memory.mcp.tools.project_management.is_factory_mode",
            return_value=True,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.ensure_workspace_project_index",
            new_callable=AsyncMock,
            return_value=workspace_index,
        ),
    ):
        result = await list_memory_projects(output_format="json")

    assert isinstance(result, dict)
    assert result["default_project"] == "personal-main"
    projects = result["projects"]
    assert len(projects) == 2
    proj = {project["name"]: project for project in projects}["cloud-proj"]
    assert proj["source"] == "cloud"
    assert proj["cloud_path"] == "/cloud-proj"
    assert proj["local_path"] is None
    assert proj["workspace_name"] == "My Org"
    assert proj["workspace_type"] == "organization"
    assert proj["workspace_tenant_id"] == "tenant-abc"
    assert proj["workspace_slug"] == "my-org"
    assert proj["workspace_is_default"] is False
    assert proj["qualified_name"] == "my-org/cloud-proj"


@pytest.mark.asyncio
async def test_list_memory_projects_factory_mode_workspace_lookup_failure(app, test_project):
    """In factory mode, workspace discovery failures are surfaced to the caller."""
    with (
        patch(
            "basic_memory.mcp.tools.project_management.is_factory_mode",
            return_value=True,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.ensure_workspace_project_index",
            new_callable=AsyncMock,
            side_effect=RuntimeError("no user context"),
        ),
    ):
        with pytest.raises(RuntimeError, match="no user context"):
            await list_memory_projects()


@pytest.mark.asyncio
async def test_list_memory_projects_json_with_cloud(app, test_project):
    """JSON output includes local_path, cloud_path, and source fields."""
    local_main = _make_project("main", "/home/user/basic-memory", is_default=True)
    local_list = _make_list([local_main], default="main")

    cloud_main = _make_project("main", "/main", id=10, external_id="cloud-main-uuid")
    cloud_only = _make_project("cloud-only", "/cloud-only", id=11, external_id="cloud-only-uuid")
    workspace = _make_workspace(
        "personal-tenant",
        "Personal",
        slug="personal",
        is_default=True,
    )
    workspace_index = _make_workspace_index([(workspace, [cloud_main, cloud_only])])

    with (
        patch(
            "basic_memory.mcp.clients.project.ProjectClient.list_projects",
            new_callable=AsyncMock,
            return_value=local_list,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.has_cloud_credentials",
            return_value=True,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.ensure_workspace_project_index",
            new_callable=AsyncMock,
            return_value=workspace_index,
        ),
    ):
        result = await list_memory_projects(output_format="json")

    assert isinstance(result, dict)
    projects = result["projects"]
    assert result["default_project"] == "main"

    # Find projects by name
    by_name = {p["name"]: p for p in projects}

    # main: local+cloud
    main_proj = by_name["main"]
    assert main_proj["source"] == "local+cloud"
    assert main_proj["local_path"] == "/home/user/basic-memory"
    assert main_proj["cloud_path"] == "/main"
    # Backward-compat: path prefers local
    assert main_proj["path"] == "/home/user/basic-memory"
    assert main_proj["is_default"] is True

    # cloud-only
    cloud_proj = by_name["cloud-only"]
    assert cloud_proj["source"] == "cloud"
    assert cloud_proj["local_path"] is None
    assert cloud_proj["cloud_path"] == "/cloud-only"
    assert cloud_proj["path"] == "/cloud-only"
    assert cloud_proj["workspace_slug"] == "personal"
    assert cloud_proj["qualified_name"] == "personal/cloud-only"


# --- Unit test for _merge_projects ---


def test_merge_projects_empty():
    """Merging two None lists produces an empty result."""
    assert _merge_projects(None, None) == []


def test_merge_projects_local_only():
    """Merging with only local projects sets source to 'local', workspace fields are None."""
    local_list = _make_list(
        [_make_project("alpha", "/alpha"), _make_project("beta", "/beta", id=2)],
        default="alpha",
    )
    merged = _merge_projects(local_list, None)
    assert len(merged) == 2
    assert all(p["source"] == "local" for p in merged)
    # Sorted by permalink
    assert merged[0]["name"] == "alpha"
    assert merged[1]["name"] == "beta"
    # Local-only projects have no workspace info
    assert all(p["workspace_name"] is None for p in merged)
    assert all(p["workspace_type"] is None for p in merged)
    assert all(p["workspace_tenant_id"] is None for p in merged)


def test_merge_projects_cloud_only():
    """Merging with only cloud projects sets source to 'cloud' with workspace info."""
    cloud_list = _make_list(
        [_make_project("gamma", "/gamma")],
        default="gamma",
    )
    merged = _merge_projects(
        None,
        cloud_list,
        cloud_workspace_name="Personal",
        cloud_workspace_type="personal",
        cloud_workspace_tenant_id="tenant-123",
    )
    assert len(merged) == 1
    assert merged[0]["source"] == "cloud"
    assert merged[0]["local_path"] is None
    assert merged[0]["cloud_path"] == "/gamma"
    assert merged[0]["workspace_name"] == "Personal"
    assert merged[0]["workspace_type"] == "personal"
    assert merged[0]["workspace_tenant_id"] == "tenant-123"


def test_merge_projects_overlap():
    """Overlapping projects carry workspace info from cloud side."""
    local_list = _make_list([_make_project("shared", "/local/shared")])
    cloud_list = _make_list([_make_project("shared", "/cloud/shared")])
    merged = _merge_projects(
        local_list,
        cloud_list,
        cloud_workspace_name="Acme Corp",
        cloud_workspace_type="organization",
        cloud_workspace_tenant_id="org-456",
    )
    assert len(merged) == 1
    assert merged[0]["source"] == "local+cloud"
    assert merged[0]["local_path"] == "/local/shared"
    assert merged[0]["cloud_path"] == "/cloud/shared"
    # Backward compat: path prefers local
    assert merged[0]["path"] == "/local/shared"
    # Cloud workspace info is present because the project has a cloud source
    assert merged[0]["workspace_name"] == "Acme Corp"
    assert merged[0]["workspace_type"] == "organization"
    assert merged[0]["workspace_tenant_id"] == "org-456"


def test_merge_workspace_projects_attaches_local_state_to_one_duplicate_workspace(tmp_path):
    """A same-name team workspace project should stay cloud-only (#848)."""
    local_path = str(tmp_path / "main")
    local_main = _make_project("main", local_path, is_default=True)
    local_list = _make_list([local_main], default="main")
    personal_main = _make_project(
        "main",
        "/cloud/personal-main",
        id=10,
        external_id="personal-main-uuid",
    )
    team_main = _make_project(
        "main",
        "/cloud/team-main",
        id=11,
        external_id="team-main-uuid",
    )
    personal_ws = _make_workspace(
        "personal-tenant",
        "Personal",
        slug="personal",
        is_default=True,
    )
    team_ws = _make_workspace(
        "team-tenant",
        "Team",
        workspace_type="organization",
        slug="team",
    )
    workspace_index = _make_workspace_index(
        [
            (personal_ws, [personal_main]),
            (team_ws, [team_main]),
        ]
    )
    config = BasicMemoryConfig(projects={"main": ProjectEntry(path=local_path)})

    merged = _merge_workspace_projects(local_list, workspace_index.entries, config=config)

    by_qualified_name = {project["qualified_name"]: project for project in merged}
    personal_project = by_qualified_name["personal/main"]
    team_project = by_qualified_name["team/main"]

    assert personal_project["source"] == "local+cloud"
    assert personal_project["local_path"] == local_path
    assert personal_project["path"] == local_path
    assert team_project["source"] == "cloud"
    assert team_project["local_path"] is None
    assert team_project["path"] == "/cloud/team-main"


def test_merge_workspace_projects_uses_configured_workspace_for_local_state(tmp_path):
    """Per-project workspace_id should select the attached duplicate row."""
    local_path = str(tmp_path / "main")
    local_main = _make_project("main", local_path, is_default=True)
    local_list = _make_list([local_main], default="main")
    personal_main = _make_project(
        "main",
        "/cloud/personal-main",
        id=10,
        external_id="personal-main-uuid",
    )
    team_main = _make_project(
        "main",
        "/cloud/team-main",
        id=11,
        external_id="team-main-uuid",
    )
    personal_ws = _make_workspace(
        "personal-tenant",
        "Personal",
        slug="personal",
        is_default=True,
    )
    team_ws = _make_workspace(
        "team-tenant",
        "Team",
        workspace_type="organization",
        slug="team",
    )
    workspace_index = _make_workspace_index(
        [
            (personal_ws, [personal_main]),
            (team_ws, [team_main]),
        ]
    )
    config = BasicMemoryConfig(
        projects={
            "main": ProjectEntry(
                path=local_path,
                workspace_id="team-tenant",
            )
        }
    )

    merged = _merge_workspace_projects(local_list, workspace_index.entries, config=config)

    by_qualified_name = {project["qualified_name"]: project for project in merged}
    personal_project = by_qualified_name["personal/main"]
    team_project = by_qualified_name["team/main"]

    assert personal_project["source"] == "cloud"
    assert personal_project["local_path"] is None
    assert personal_project["path"] == "/cloud/personal-main"
    assert team_project["source"] == "local+cloud"
    assert team_project["local_path"] == local_path
    assert team_project["path"] == local_path


def test_merge_workspace_projects_uses_default_workspace_for_local_state(tmp_path):
    """Global default_workspace should attach local state before cloud default fallback."""
    local_path = str(tmp_path / "main")
    local_main = _make_project("main", local_path, is_default=True)
    local_list = _make_list([local_main], default="main")
    personal_main = _make_project(
        "main",
        "/cloud/personal-main",
        id=10,
        external_id="personal-main-uuid",
    )
    team_main = _make_project(
        "main",
        "/cloud/team-main",
        id=11,
        external_id="team-main-uuid",
    )
    personal_ws = _make_workspace(
        "personal-tenant",
        "Personal",
        slug="personal",
        is_default=True,
    )
    team_ws = _make_workspace(
        "team-tenant",
        "Team",
        workspace_type="organization",
        slug="team",
    )
    workspace_index = _make_workspace_index(
        [
            (personal_ws, [personal_main]),
            (team_ws, [team_main]),
        ]
    )
    config = BasicMemoryConfig(
        projects={"main": ProjectEntry(path=local_path)},
        default_workspace="team-tenant",
    )

    merged = _merge_workspace_projects(local_list, workspace_index.entries, config=config)

    by_qualified_name = {project["qualified_name"]: project for project in merged}
    personal_project = by_qualified_name["personal/main"]
    team_project = by_qualified_name["team/main"]

    assert personal_project["source"] == "cloud"
    assert personal_project["local_path"] is None
    assert personal_project["path"] == "/cloud/personal-main"
    assert team_project["source"] == "local+cloud"
    assert team_project["local_path"] == local_path
    assert team_project["path"] == local_path


def test_merge_workspace_projects_sorted_fallback_attaches_personal_workspace(tmp_path):
    """When config has no preference and no cloud default exists, use stable priority."""
    local_path = str(tmp_path / "main")
    local_main = _make_project("main", local_path, is_default=True)
    local_list = _make_list([local_main], default="main")
    personal_main = _make_project(
        "main",
        "/cloud/personal-main",
        id=10,
        external_id="personal-main-uuid",
    )
    team_main = _make_project(
        "main",
        "/cloud/team-main",
        id=11,
        external_id="team-main-uuid",
    )
    personal_ws = _make_workspace(
        "personal-tenant",
        "Personal",
        slug="personal",
        is_default=False,
    )
    team_ws = _make_workspace(
        "team-tenant",
        "Team",
        workspace_type="organization",
        slug="team",
    )
    workspace_index = _make_workspace_index(
        [
            (team_ws, [team_main]),
            (personal_ws, [personal_main]),
        ]
    )
    config = BasicMemoryConfig(projects={"main": ProjectEntry(path=local_path)})

    merged = _merge_workspace_projects(local_list, workspace_index.entries, config=config)

    by_qualified_name = {project["qualified_name"]: project for project in merged}
    personal_project = by_qualified_name["personal/main"]
    team_project = by_qualified_name["team/main"]

    assert personal_project["source"] == "local+cloud"
    assert personal_project["local_path"] == local_path
    assert personal_project["path"] == local_path
    assert team_project["source"] == "cloud"
    assert team_project["local_path"] is None
    assert team_project["path"] == "/cloud/team-main"


# --- Workspace passthrough tests ---


def _make_workspace(
    tenant_id: str,
    name: str,
    workspace_type: str = "personal",
    role: str = "owner",
    organization_id: str | None = None,
    slug: str | None = None,
    is_default: bool = False,
):
    """Create a WorkspaceInfo for testing."""
    from basic_memory.schemas.cloud import WorkspaceInfo

    return WorkspaceInfo(
        tenant_id=tenant_id,
        name=name,
        workspace_type=workspace_type,
        slug=slug or name.casefold().replace(" ", "-"),
        role=role,
        organization_id=organization_id,
        is_default=is_default,
        has_active_subscription=True,
    )


def _make_workspace_index(workspace_projects):
    """Create a WorkspaceProjectIndex from (workspace, projects) tuples."""
    from basic_memory.mcp.project_context import (
        WorkspaceProjectEntry,
        _build_workspace_project_index,
    )

    workspaces = tuple(workspace for workspace, _projects in workspace_projects)
    entries = tuple(
        WorkspaceProjectEntry(workspace=workspace, project=project)
        for workspace, projects in workspace_projects
        for project in projects
    )
    return _build_workspace_project_index(workspaces, entries)


@pytest.mark.asyncio
async def test_list_memory_projects_aggregates_without_config_workspace(app, test_project):
    """When no explicit workspace is given, cloud discovery fans out across workspaces."""
    cloud_project = _make_project("cloud-proj", "/cloud-proj")
    workspace = _make_workspace(
        "config-default-ws",
        "Default WS",
        slug="default",
        is_default=True,
    )
    workspace_index = _make_workspace_index([(workspace, [cloud_project])])

    with (
        patch("basic_memory.mcp.tools.project_management.ConfigManager") as mock_cm_cls,
        patch(
            "basic_memory.mcp.tools.project_management.has_cloud_credentials",
            return_value=True,
        ),
        patch(
            "basic_memory.mcp.tools.project_management.ensure_workspace_project_index",
            new_callable=AsyncMock,
            return_value=workspace_index,
        ) as mock_index,
    ):
        mock_config = mock_cm_cls.return_value.config
        mock_config.default_workspace = "config-default-ws"
        result = await list_memory_projects()

    mock_index.assert_awaited_once()
    assert "- cloud-proj (cloud) [00000000-0000-0000-0000-000000000001]" in result
