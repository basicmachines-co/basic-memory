"""Tests for project list display and project ls routing behavior."""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.mcp.clients.project import ProjectClient
from basic_memory.schemas.cloud import WorkspaceInfo
from basic_memory.schemas.project_info import ProjectList

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
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None

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
    async def fake_get_client(workspace=None):
        yield object()

    monkeypatch.setattr(project_cmd, "get_client", fake_get_client)


def _workspace(
    *,
    tenant_id: str,
    slug: str,
    name: str,
    workspace_type: str,
    is_default: bool = False,
) -> WorkspaceInfo:
    return WorkspaceInfo(
        tenant_id=tenant_id,
        workspace_type=workspace_type,
        slug=slug,
        name=name,
        role="owner",
        is_default=is_default,
        organization_id=None,
        has_active_subscription=True,
    )


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
                    "local_sync_path": beta_local_sync,
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

    _original_list_projects = ProjectClient.list_projects

    async def fake_list_projects(self):
        if os.getenv("BASIC_MEMORY_FORCE_CLOUD", "").lower() in ("true", "1", "yes"):
            return ProjectList.model_validate(cloud_payload)
        return ProjectList.model_validate(local_payload)

    monkeypatch.setattr(ProjectClient, "list_projects", fake_list_projects)

    result = runner.invoke(app, ["project", "list"], env={"COLUMNS": "240"})

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.stdout}"
    assert "Local Path" in result.stdout
    assert "Cloud Path" in result.stdout
    assert "CLI Route" in result.stdout
    assert "MCP" in result.stdout

    lines = result.stdout.splitlines()
    alpha_line = next(line for line in lines if "│ alpha" in line)
    beta_line = next(line for line in lines if "│ beta" in line)

    assert "local" in alpha_line  # CLI route for alpha
    assert "stdio" in alpha_line  # Local projects use stdio transport
    assert "cloud" in beta_line  # CLI route for beta
    assert "https" in beta_line  # Cloud projects use HTTPS transport
    assert "alpha-local" in result.stdout
    assert "/alpha" in result.stdout
    assert "/beta" in result.stdout


def test_project_list_shows_display_name_for_private_projects(
    runner: CliRunner, write_config, mock_client, tmp_path, monkeypatch
):
    """Private projects should show display_name ('My Project') instead of raw UUID name."""
    private_uuid = "f1df8f39-d5aa-4095-ae05-8c5a2883029a"

    write_config(
        {
            "env": "dev",
            "projects": {},
            "default_project": "main",
            "cloud_api_key": "bmc_test_key_123",
        }
    )

    local_payload = {
        "projects": [
            {
                "id": 1,
                "external_id": "11111111-1111-1111-1111-111111111111",
                "name": "main",
                "path": "/main",
                "is_default": True,
            }
        ],
        "default_project": "main",
    }

    cloud_payload = {
        "projects": [
            {
                "id": 1,
                "external_id": "11111111-1111-1111-1111-111111111111",
                "name": "main",
                "path": "/main",
                "is_default": True,
            },
            {
                "id": 2,
                "external_id": "22222222-2222-2222-2222-222222222222",
                "name": private_uuid,
                "path": f"/{private_uuid}",
                "is_default": False,
                "display_name": "My Project",
                "is_private": True,
            },
        ],
        "default_project": "main",
    }

    async def fake_list_projects(self):
        if os.getenv("BASIC_MEMORY_FORCE_CLOUD", "").lower() in ("true", "1", "yes"):
            return ProjectList.model_validate(cloud_payload)
        return ProjectList.model_validate(local_payload)

    monkeypatch.setattr(ProjectClient, "list_projects", fake_list_projects)

    result = runner.invoke(app, ["project", "list"], env={"COLUMNS": "240"})

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.stdout}"
    # Rich table should show display_name in the Name column
    assert "My Project" in result.stdout
    lines = result.stdout.splitlines()
    project_line = next(line for line in lines if "My Project" in line)
    name_cell = project_line.split("│")[1].strip()
    assert name_cell == "My Project"

    # JSON output should preserve canonical name for scripting, with display_name as separate field
    json_result = runner.invoke(app, ["project", "list", "--json"], env={"COLUMNS": "240"})
    assert json_result.exit_code == 0
    data = json.loads(json_result.stdout)
    private_project = next(p for p in data["projects"] if p.get("display_name") == "My Project")
    assert private_project["name"] == private_uuid
    assert private_project["display_name"] == "My Project"


def test_project_list_cloud_fetches_all_workspaces_and_labels_duplicate_permalinks(
    runner: CliRunner, write_config, monkeypatch
):
    """Cloud project list should include every workspace without collapsing matching names."""
    write_config(
        {
            "env": "dev",
            "projects": {},
            "default_project": None,
            "cloud_api_key": "bmc_test_key_123",
        }
    )

    personal = _workspace(
        tenant_id="tenant-personal",
        slug="personal",
        name="Personal",
        workspace_type="personal",
        is_default=True,
    )
    team = _workspace(
        tenant_id="tenant-team",
        slug="team",
        name="Team",
        workspace_type="organization",
    )

    async def fake_get_available_workspaces():
        return [personal, team]

    class FakeClient:
        def __init__(self, workspace: str | None):
            self.workspace = workspace

    @asynccontextmanager
    async def fake_get_client(workspace=None):
        yield FakeClient(workspace)

    payloads_by_workspace = {
        None: {"projects": [], "default_project": None},
        "tenant-personal": {
            "projects": [
                {
                    "id": 1,
                    "external_id": "11111111-1111-1111-1111-111111111111",
                    "name": "shared",
                    "path": "/personal/shared",
                    "is_default": True,
                }
            ],
            "default_project": "shared",
        },
        "tenant-team": {
            "projects": [
                {
                    "id": 2,
                    "external_id": "22222222-2222-2222-2222-222222222222",
                    "name": "shared",
                    "path": "/team/shared",
                    "is_default": False,
                }
            ],
            "default_project": None,
        },
    }
    seen_workspaces: list[str | None] = []

    async def fake_list_projects(self):
        workspace = self.http_client.workspace
        seen_workspaces.append(workspace)
        return ProjectList.model_validate(payloads_by_workspace[workspace])

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fake_get_available_workspaces,
    )
    monkeypatch.setattr(project_cmd, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "list_projects", fake_list_projects)

    result = runner.invoke(app, ["project", "list", "--json"], env={"COLUMNS": "240"})

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.stdout}"
    # The initial None call is the local project fetch before cloud workspaces are listed.
    assert seen_workspaces == [None, "tenant-personal", "tenant-team"]

    data = json.loads(result.stdout)
    shared_projects = [project for project in data["projects"] if project["name"] == "shared"]
    assert len(shared_projects) == 2
    assert {project["cloud_path"] for project in shared_projects} == {
        "/personal/shared",
        "/team/shared",
    }
    assert {project["workspace"] for project in shared_projects} == {"Personal", "Team"}
    assert {project["workspace_type"] for project in shared_projects} == {
        "personal",
        "organization",
    }

    table_result = runner.invoke(app, ["project", "list"], env={"COLUMNS": "240"})

    assert table_result.exit_code == 0
    assert "Personal (personal)" in table_result.stdout
    assert "Team (organization)" in table_result.stdout


def test_project_list_workspace_discovery_failure_warns_and_uses_fallback(
    runner: CliRunner, write_config, monkeypatch
):
    """Workspace discovery failures should fall back and explain the degraded result."""
    write_config(
        {
            "env": "dev",
            "projects": {},
            "default_project": None,
            "default_workspace": "tenant-default",
            "cloud_api_key": "bmc_test_key_123",
        }
    )

    class FakeClient:
        def __init__(self, workspace: str | None):
            self.workspace = workspace

    @asynccontextmanager
    async def fake_get_client(workspace=None):
        yield FakeClient(workspace)

    async def fail_get_available_workspaces():
        raise RuntimeError("workspace service unavailable")

    payloads_by_workspace = {
        None: {"projects": [], "default_project": None},
        "tenant-default": {
            "projects": [
                {
                    "id": 1,
                    "external_id": "11111111-1111-1111-1111-111111111111",
                    "name": "fallback-project",
                    "path": "/fallback-project",
                    "is_default": False,
                }
            ],
            "default_project": None,
        },
    }

    async def fake_list_projects(self):
        return ProjectList.model_validate(payloads_by_workspace[self.http_client.workspace])

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fail_get_available_workspaces,
    )
    monkeypatch.setattr(project_cmd, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "list_projects", fake_list_projects)

    result = runner.invoke(app, ["project", "list"], env={"COLUMNS": "240"})

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.stdout}"
    assert "fallback-project" in result.stdout
    assert "Cloud workspace discovery failed: workspace service unavailable" in result.stdout
    assert "Showing cloud projects from the configured/default workspace only" in result.stdout


def test_project_list_partial_workspace_failure_warns_and_keeps_successes(
    runner: CliRunner, write_config, monkeypatch
):
    """A failed workspace fetch should not hide projects from successful workspaces."""
    write_config(
        {
            "env": "dev",
            "projects": {},
            "default_project": None,
            "cloud_api_key": "bmc_test_key_123",
        }
    )

    personal = _workspace(
        tenant_id="tenant-personal",
        slug="personal",
        name="Personal",
        workspace_type="personal",
        is_default=True,
    )
    team = _workspace(
        tenant_id="tenant-team",
        slug="team",
        name="Team",
        workspace_type="organization",
    )

    async def fake_get_available_workspaces():
        return [personal, team]

    class FakeClient:
        def __init__(self, workspace: str | None):
            self.workspace = workspace

    @asynccontextmanager
    async def fake_get_client(workspace=None):
        yield FakeClient(workspace)

    payloads_by_workspace = {
        None: {"projects": [], "default_project": None},
        "tenant-personal": {
            "projects": [
                {
                    "id": 1,
                    "external_id": "11111111-1111-1111-1111-111111111111",
                    "name": "personal-project",
                    "path": "/personal-project",
                    "is_default": False,
                }
            ],
            "default_project": None,
        },
    }

    async def fake_list_projects(self):
        if self.http_client.workspace == "tenant-team":
            raise RuntimeError("team unavailable")
        return ProjectList.model_validate(payloads_by_workspace[self.http_client.workspace])

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fake_get_available_workspaces,
    )
    monkeypatch.setattr(project_cmd, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "list_projects", fake_list_projects)

    result = runner.invoke(app, ["project", "list"], env={"COLUMNS": "240"})

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.stdout}"
    assert "personal-project" in result.stdout
    assert "Cloud project discovery failed for workspace Team: team unavailable" in result.stdout


def test_project_list_workspace_type_filter_selects_unique_workspace(
    runner: CliRunner, write_config, monkeypatch
):
    """--workspace can use a workspace type when it resolves to one workspace."""
    write_config(
        {
            "env": "dev",
            "projects": {},
            "default_project": None,
            "cloud_api_key": "bmc_test_key_123",
        }
    )

    personal = _workspace(
        tenant_id="tenant-personal",
        slug="personal",
        name="Personal",
        workspace_type="personal",
        is_default=True,
    )
    team = _workspace(
        tenant_id="tenant-team",
        slug="team",
        name="Team",
        workspace_type="organization",
    )

    async def fake_get_available_workspaces():
        return [personal, team]

    class FakeClient:
        def __init__(self, workspace: str | None):
            self.workspace = workspace

    @asynccontextmanager
    async def fake_get_client(workspace=None):
        yield FakeClient(workspace)

    payloads_by_workspace = {
        None: {"projects": [], "default_project": None},
        "tenant-team": {
            "projects": [
                {
                    "id": 1,
                    "external_id": "11111111-1111-1111-1111-111111111111",
                    "name": "team-project",
                    "path": "/team-project",
                    "is_default": True,
                }
            ],
            "default_project": "team-project",
        },
    }
    seen_workspaces: list[str | None] = []

    async def fake_list_projects(self):
        workspace = self.http_client.workspace
        seen_workspaces.append(workspace)
        return ProjectList.model_validate(payloads_by_workspace[workspace])

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fake_get_available_workspaces,
    )
    monkeypatch.setattr(project_cmd, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "list_projects", fake_list_projects)

    result = runner.invoke(
        app,
        ["project", "list", "--workspace", "organization", "--json"],
        env={"COLUMNS": "240"},
    )

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.stdout}"
    assert seen_workspaces == [None, "tenant-team"]
    data = json.loads(result.stdout)
    assert [project["name"] for project in data["projects"]] == ["team-project"]
    assert data["projects"][0]["workspace"] == "Team"


def test_project_list_workspace_type_filter_lists_ambiguous_matches(
    runner: CliRunner, write_config, monkeypatch
):
    """Ambiguous workspace type filters should list copyable matching slugs."""
    write_config(
        {
            "env": "dev",
            "projects": {},
            "default_project": None,
            "cloud_api_key": "bmc_test_key_123",
        }
    )

    async def fake_get_available_workspaces():
        return [
            _workspace(
                tenant_id="tenant-personal",
                slug="personal",
                name="Personal",
                workspace_type="personal",
                is_default=True,
            ),
            _workspace(
                tenant_id="tenant-team-alpha",
                slug="team-alpha",
                name="Team Alpha",
                workspace_type="organization",
            ),
            _workspace(
                tenant_id="tenant-team-beta",
                slug="team-beta",
                name="Team Beta",
                workspace_type="organization",
            ),
        ]

    @asynccontextmanager
    async def fake_get_client(workspace=None):
        yield object()

    async def fake_list_projects(self):
        return ProjectList.model_validate({"projects": [], "default_project": None})

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fake_get_available_workspaces,
    )
    monkeypatch.setattr(project_cmd, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "list_projects", fake_list_projects)

    result = runner.invoke(
        app,
        ["project", "list", "--workspace", "organization"],
        env={"COLUMNS": "240"},
    )

    assert result.exit_code == 1
    assert "Workspace 'organization' matches multiple workspaces" in result.stdout
    assert "Choose one of these matching workspaces by slug" in result.stdout
    assert "workspace: team-alpha" in result.stdout
    assert "workspace: team-beta" in result.stdout
    assert "tenant_id: tenant-team-alpha" in result.stdout
    assert "tenant_id: tenant-team-beta" in result.stdout
    assert "workspace: personal" not in result.stdout


def test_project_list_invalid_workspace_exits_without_local_fallback(
    runner: CliRunner, write_config, tmp_path, monkeypatch
):
    """Invalid explicit workspace filters should stop instead of showing local-only rows."""
    local_path = (tmp_path / "main").as_posix()
    write_config(
        {
            "env": "dev",
            "projects": {"main": {"path": local_path, "mode": "local"}},
            "default_project": "main",
            "cloud_api_key": "bmc_test_key_123",
        }
    )

    personal = _workspace(
        tenant_id="tenant-personal",
        slug="personal",
        name="Personal",
        workspace_type="personal",
        is_default=True,
    )

    async def fake_get_available_workspaces():
        return [personal]

    class FakeClient:
        def __init__(self, workspace: str | None):
            self.workspace = workspace

    @asynccontextmanager
    async def fake_get_client(workspace=None):
        yield FakeClient(workspace)

    async def fake_list_projects(self):
        return ProjectList.model_validate(
            {
                "projects": [
                    {
                        "id": 1,
                        "external_id": "11111111-1111-1111-1111-111111111111",
                        "name": "main",
                        "path": local_path,
                        "is_default": True,
                    }
                ],
                "default_project": "main",
            }
        )

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fake_get_available_workspaces,
    )
    monkeypatch.setattr(project_cmd, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "list_projects", fake_list_projects)

    result = runner.invoke(
        app,
        ["project", "list", "--workspace", "missing"],
        env={"COLUMNS": "240"},
    )

    assert result.exit_code == 1
    assert "Workspace 'missing' not found" in result.stdout
    assert "Basic Memory Projects" not in result.stdout
    assert "Cloud project discovery failed" not in result.stdout


def test_project_list_attaches_local_state_to_one_duplicate_cloud_project(
    runner: CliRunner, write_config, tmp_path, monkeypatch
):
    """Local path/default/sync state should not appear on every matching workspace."""
    local_path = (tmp_path / "main").as_posix()
    write_config(
        {
            "env": "dev",
            "projects": {
                "main": {
                    "path": local_path,
                    "mode": "local",
                    "local_sync_path": local_path,
                }
            },
            "default_project": "main",
            "cloud_api_key": "bmc_test_key_123",
        }
    )

    personal = _workspace(
        tenant_id="tenant-personal",
        slug="personal",
        name="Personal",
        workspace_type="personal",
        is_default=True,
    )
    team = _workspace(
        tenant_id="tenant-team",
        slug="team",
        name="Team",
        workspace_type="organization",
    )

    async def fake_get_available_workspaces():
        return [personal, team]

    class FakeClient:
        def __init__(self, workspace: str | None):
            self.workspace = workspace

    @asynccontextmanager
    async def fake_get_client(workspace=None):
        yield FakeClient(workspace)

    payloads_by_workspace = {
        None: {
            "projects": [
                {
                    "id": 1,
                    "external_id": "11111111-1111-1111-1111-111111111111",
                    "name": "main",
                    "path": local_path,
                    "is_default": True,
                }
            ],
            "default_project": "main",
        },
        "tenant-personal": {
            "projects": [
                {
                    "id": 2,
                    "external_id": "22222222-2222-2222-2222-222222222222",
                    "name": "main",
                    "path": "/basic-memory",
                    "is_default": True,
                }
            ],
            "default_project": "main",
        },
        "tenant-team": {
            "projects": [
                {
                    "id": 3,
                    "external_id": "33333333-3333-3333-3333-333333333333",
                    "name": "main",
                    "path": "/basic-memory",
                    "is_default": True,
                }
            ],
            "default_project": "main",
        },
    }

    async def fake_list_projects(self):
        return ProjectList.model_validate(payloads_by_workspace[self.http_client.workspace])

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fake_get_available_workspaces,
    )
    monkeypatch.setattr(project_cmd, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "list_projects", fake_list_projects)

    result = runner.invoke(app, ["project", "list", "--json"], env={"COLUMNS": "240"})

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.stdout}"
    data = json.loads(result.stdout)
    main_rows = [project for project in data["projects"] if project["name"] == "main"]
    assert len(main_rows) == 2

    personal_row = next(project for project in main_rows if project["workspace"] == "Personal")
    team_row = next(project for project in main_rows if project["workspace"] == "Team")

    assert personal_row["local_path"] == project_cmd.format_path(local_path)
    assert personal_row["cli_route"] == "local"
    assert personal_row["mcp_stdio"] == "stdio"
    assert personal_row["sync"] is True
    assert personal_row["is_default"] is True

    assert team_row["local_path"] == ""
    assert team_row["cli_route"] == "cloud"
    assert team_row["mcp_stdio"] == "https"
    assert team_row["sync"] is False
    assert team_row["is_default"] is False

    filtered_result = runner.invoke(
        app,
        ["project", "list", "--workspace", "organization", "--json"],
        env={"COLUMNS": "240"},
    )

    assert filtered_result.exit_code == 0, (
        f"Exit code: {filtered_result.exit_code}, output: {filtered_result.stdout}"
    )
    filtered_data = json.loads(filtered_result.stdout)
    assert len(filtered_data["projects"]) == 1
    filtered_team_row = filtered_data["projects"][0]
    assert filtered_team_row["workspace"] == "Team"
    assert filtered_team_row["local_path"] == ""
    assert filtered_team_row["cli_route"] == "cloud"
    assert filtered_team_row["mcp_stdio"] == "https"
    assert filtered_team_row["sync"] is False
    assert filtered_team_row["is_default"] is False


def test_project_list_hides_bisync_flag_for_attached_team_workspace(
    runner: CliRunner, write_config, tmp_path, monkeypatch
):
    """Bisync is only supported for personal workspaces."""
    local_path = (tmp_path / "team-main").as_posix()
    write_config(
        {
            "env": "dev",
            "projects": {
                "main": {
                    "path": local_path,
                    "mode": "cloud",
                    "workspace_id": "tenant-team",
                    "local_sync_path": local_path,
                }
            },
            "default_project": "main",
            "cloud_api_key": "bmc_test_key_123",
        }
    )

    personal = _workspace(
        tenant_id="tenant-personal",
        slug="personal",
        name="Personal",
        workspace_type="personal",
        is_default=True,
    )
    team = _workspace(
        tenant_id="tenant-team",
        slug="team",
        name="Team",
        workspace_type="organization",
    )

    async def fake_get_available_workspaces():
        return [personal, team]

    class FakeClient:
        def __init__(self, workspace: str | None):
            self.workspace = workspace

    @asynccontextmanager
    async def fake_get_client(workspace=None):
        yield FakeClient(workspace)

    project_payload = {
        "projects": [
            {
                "id": 1,
                "external_id": "11111111-1111-1111-1111-111111111111",
                "name": "main",
                "path": "/basic-memory",
                "is_default": True,
            }
        ],
        "default_project": "main",
    }
    payloads_by_workspace = {
        None: {"projects": [], "default_project": None},
        "tenant-personal": project_payload,
        "tenant-team": project_payload,
    }

    async def fake_list_projects(self):
        return ProjectList.model_validate(payloads_by_workspace[self.http_client.workspace])

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fake_get_available_workspaces,
    )
    monkeypatch.setattr(project_cmd, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "list_projects", fake_list_projects)

    result = runner.invoke(app, ["project", "list", "--json"], env={"COLUMNS": "240"})

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.stdout}"
    data = json.loads(result.stdout)
    main_rows = [project for project in data["projects"] if project["name"] == "main"]
    team_row = next(project for project in main_rows if project["workspace"] == "Team")

    assert team_row["cli_route"] == "cloud"
    assert team_row["mcp_stdio"] == "https"
    assert team_row["sync"] is False
    assert team_row["is_default"] is True


def test_project_ls_local_mode_defaults_to_local_route(
    runner: CliRunner, write_config, mock_client, tmp_path, monkeypatch
):
    """project ls without flags for a local-mode project should list local files."""
    project_dir = tmp_path / "alpha-files"
    (project_dir / "docs").mkdir(parents=True, exist_ok=True)
    (project_dir / "notes.md").write_text("# local note")
    (project_dir / "docs" / "spec.md").write_text("# spec")

    write_config(
        {
            "env": "dev",
            "projects": {"alpha": {"path": project_dir.as_posix(), "mode": "local"}},
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

    async def fake_list_projects(self):
        assert os.getenv("BASIC_MEMORY_FORCE_CLOUD", "").lower() not in ("true", "1", "yes")
        return ProjectList.model_validate(payload)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("project_ls should not be used for default local route")

    monkeypatch.setattr(ProjectClient, "list_projects", fake_list_projects)
    monkeypatch.setattr(project_cmd, "project_ls", fail_if_called)

    result = runner.invoke(app, ["project", "ls", "--name", "alpha"], env={"COLUMNS": "200"})

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.stdout}"
    assert "Files in alpha (LOCAL)" in result.stdout
    assert "notes.md" in result.stdout
    assert "docs/spec.md" in result.stdout


def test_project_ls_cloud_mode_defaults_to_cloud_route(
    runner: CliRunner, write_config, mock_client, tmp_path, monkeypatch
):
    """project ls without flags for a cloud-mode project should list cloud files."""
    write_config(
        {
            "env": "dev",
            "projects": {"alpha": {"path": str(tmp_path / "alpha"), "mode": "cloud"}},
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

    class _TenantInfo:
        bucket_name = "tenant-bucket"

    async def fake_list_projects(self):
        # Cloud routing should be active when project mode is cloud
        assert os.getenv("BASIC_MEMORY_FORCE_CLOUD", "").lower() in ("true", "1", "yes")
        return ProjectList.model_validate(cloud_payload)

    async def fake_get_mount_info():
        return _TenantInfo()

    monkeypatch.setattr(ProjectClient, "list_projects", fake_list_projects)
    monkeypatch.setattr(project_cmd, "get_mount_info", fake_get_mount_info)
    monkeypatch.setattr(project_cmd, "project_ls", lambda *args, **kwargs: ["        42 cloud.md"])

    # No --cloud flag: project mode should determine route
    result = runner.invoke(app, ["project", "ls", "--name", "alpha"], env={"COLUMNS": "200"})

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.stdout}"
    assert "Files in alpha (CLOUD)" in result.stdout
    assert "cloud.md" in result.stdout


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

    class _TenantInfo:
        bucket_name = "tenant-bucket"

    async def fake_list_projects(self):
        assert os.getenv("BASIC_MEMORY_FORCE_CLOUD", "").lower() in ("true", "1", "yes")
        return ProjectList.model_validate(cloud_payload)

    async def fake_get_mount_info():
        return _TenantInfo()

    monkeypatch.setattr(ProjectClient, "list_projects", fake_list_projects)
    monkeypatch.setattr(project_cmd, "get_mount_info", fake_get_mount_info)
    monkeypatch.setattr(project_cmd, "project_ls", lambda *args, **kwargs: ["        42 cloud.md"])

    result = runner.invoke(app, ["project", "ls", "--name", "alpha", "--cloud"])

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.stdout}"
    assert "Files in alpha (CLOUD)" in result.stdout
    assert "cloud.md" in result.stdout
