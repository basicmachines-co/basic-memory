"""Tests for MCP project management tools."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from basic_memory import db
from basic_memory.mcp.tools import list_memory_projects, create_memory_project, delete_project
from basic_memory.mcp.tools.project_management import _merge_projects
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
    assert f"• {test_project.name}" in result


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
    assert "• main (local)" in result
    # Private project shows display_name with slug in parentheses, then source
    assert "• My Notes (private-fb83af23) (local)" in result


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

    assert "• my-project (local)" in result


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
    cloud_list = _make_list([cloud_main, cloud_llc], default="main")

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
            "basic_memory.mcp.tools.project_management._fetch_cloud_projects",
            new_callable=AsyncMock,
            return_value=cloud_list,
        ),
    ):
        result = await list_memory_projects()

    # Both local+cloud project shows merged source
    assert "• main (local+cloud)" in result
    # Local-only project
    assert "• specs (local)" in result
    # Cloud-only project
    assert "• basic-memory-llc (cloud)" in result


@pytest.mark.asyncio
async def test_list_memory_projects_no_cloud_credentials(app, test_project):
    """When no cloud credentials exist, only local projects are shown."""
    with patch(
        "basic_memory.mcp.tools.project_management.has_cloud_credentials",
        return_value=False,
    ):
        result = await list_memory_projects()

    assert "Available projects:" in result
    assert f"• {test_project.name} (local)" in result
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
            "basic_memory.mcp.tools.project_management._fetch_cloud_projects",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await list_memory_projects()

    assert "Available projects:" in result
    assert f"• {test_project.name} (local)" in result


@pytest.mark.asyncio
async def test_list_memory_projects_factory_mode(app, test_project):
    """In factory mode (cloud app), only the factory client is used — no cloud merge."""
    factory_project = _make_project("cloud-proj", "/cloud-proj", is_default=True)
    factory_list = _make_list([factory_project], default="cloud-proj")

    with (
        patch(
            "basic_memory.mcp.tools.project_management.is_factory_mode",
            return_value=True,
        ),
        patch(
            "basic_memory.mcp.clients.project.ProjectClient.list_projects",
            new_callable=AsyncMock,
            return_value=factory_list,
        ),
    ):
        result = await list_memory_projects()

    assert "• cloud-proj (local)" in result
    # has_cloud_credentials should not be called in factory mode
    # (no cloud merge attempt)


@pytest.mark.asyncio
async def test_list_memory_projects_json_with_cloud(app, test_project):
    """JSON output includes local_path, cloud_path, and source fields."""
    local_main = _make_project("main", "/home/user/basic-memory", is_default=True)
    local_list = _make_list([local_main], default="main")

    cloud_main = _make_project("main", "/main", id=10, external_id="cloud-main-uuid")
    cloud_only = _make_project("cloud-only", "/cloud-only", id=11, external_id="cloud-only-uuid")
    cloud_list = _make_list([cloud_main, cloud_only], default="main")

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
            "basic_memory.mcp.tools.project_management._fetch_cloud_projects",
            new_callable=AsyncMock,
            return_value=cloud_list,
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


# --- Workspace passthrough tests ---


def _make_workspace(
    tenant_id: str, name: str, workspace_type: str = "personal", role: str = "owner"
):
    """Create a WorkspaceInfo for testing."""
    from basic_memory.schemas.cloud import WorkspaceInfo

    return WorkspaceInfo(
        tenant_id=tenant_id,
        name=name,
        workspace_type=workspace_type,
        role=role,
        has_active_subscription=True,
    )


@pytest.mark.asyncio
async def test_list_memory_projects_passes_explicit_workspace(app, test_project):
    """Explicit workspace param is forwarded to _fetch_cloud_projects."""
    cloud_list = _make_list([_make_project("cloud-proj", "/cloud-proj")])

    with (
        patch(
            "basic_memory.mcp.tools.project_management.has_cloud_credentials",
            return_value=True,
        ),
        patch(
            "basic_memory.mcp.tools.project_management._fetch_cloud_projects",
            new_callable=AsyncMock,
            return_value=cloud_list,
        ) as mock_fetch,
        patch(
            "basic_memory.mcp.project_context.get_available_workspaces",
            new_callable=AsyncMock,
            return_value=[_make_workspace("my-org-tenant-id", "My Org", "organization")],
        ),
    ):
        await list_memory_projects(workspace="my-org-tenant-id")

    mock_fetch.assert_awaited_once_with("my-org-tenant-id", None)


@pytest.mark.asyncio
async def test_list_memory_projects_falls_back_to_config_workspace(app, test_project):
    """When no explicit workspace is given, config.default_workspace is used."""
    cloud_list = _make_list([_make_project("cloud-proj", "/cloud-proj")])

    with (
        patch("basic_memory.mcp.tools.project_management.ConfigManager") as mock_cm_cls,
        patch(
            "basic_memory.mcp.tools.project_management.has_cloud_credentials",
            return_value=True,
        ),
        patch(
            "basic_memory.mcp.tools.project_management._fetch_cloud_projects",
            new_callable=AsyncMock,
            return_value=cloud_list,
        ) as mock_fetch,
        patch(
            "basic_memory.mcp.project_context.get_available_workspaces",
            new_callable=AsyncMock,
            return_value=[_make_workspace("config-default-ws", "Default WS")],
        ),
    ):
        mock_config = mock_cm_cls.return_value.config
        mock_config.default_workspace = "config-default-ws"
        await list_memory_projects()

    mock_fetch.assert_awaited_once_with("config-default-ws", None)


@pytest.mark.asyncio
async def test_list_memory_projects_explicit_workspace_overrides_config(app, test_project):
    """Explicit workspace takes precedence over config.default_workspace."""
    cloud_list = _make_list([_make_project("cloud-proj", "/cloud-proj")])

    with (
        patch("basic_memory.mcp.tools.project_management.ConfigManager") as mock_cm_cls,
        patch(
            "basic_memory.mcp.tools.project_management.has_cloud_credentials",
            return_value=True,
        ),
        patch(
            "basic_memory.mcp.tools.project_management._fetch_cloud_projects",
            new_callable=AsyncMock,
            return_value=cloud_list,
        ) as mock_fetch,
        patch(
            "basic_memory.mcp.project_context.get_available_workspaces",
            new_callable=AsyncMock,
            return_value=[_make_workspace("explicit-ws", "Explicit WS", "organization")],
        ),
    ):
        mock_config = mock_cm_cls.return_value.config
        mock_config.default_workspace = "config-default-ws"
        await list_memory_projects(workspace="explicit-ws")

    # Explicit workspace wins over config default
    mock_fetch.assert_awaited_once_with("explicit-ws", None)


@pytest.mark.asyncio
async def test_list_memory_projects_json_includes_workspace_info(app, test_project):
    """JSON output includes workspace_name, workspace_type, workspace_tenant_id for cloud projects."""
    local_proj = _make_project("local-only", "/local/path", is_default=True)
    local_list = _make_list([local_proj], default="local-only")

    cloud_proj = _make_project("cloud-proj", "/cloud/path", id=10, external_id="cloud-uuid")
    cloud_list = _make_list([cloud_proj])

    ws = _make_workspace("org-tenant-abc", "Acme Corp", "organization")

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
            "basic_memory.mcp.tools.project_management._fetch_cloud_projects",
            new_callable=AsyncMock,
            return_value=cloud_list,
        ),
        patch(
            "basic_memory.mcp.project_context.get_available_workspaces",
            new_callable=AsyncMock,
            return_value=[ws],
        ),
    ):
        result = await list_memory_projects(output_format="json", workspace="org-tenant-abc")

    by_name = {p["name"]: p for p in result["projects"]}

    # Cloud project carries workspace info
    cloud = by_name["cloud-proj"]
    assert cloud["workspace_name"] == "Acme Corp"
    assert cloud["workspace_type"] == "organization"
    assert cloud["workspace_tenant_id"] == "org-tenant-abc"

    # Local-only project has no workspace info
    local = by_name["local-only"]
    assert local["workspace_name"] is None
    assert local["workspace_type"] is None
    assert local["workspace_tenant_id"] is None
