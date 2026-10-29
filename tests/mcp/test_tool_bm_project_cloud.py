"""bm_project and bm_project_delete against Cloud workspaces.

The Cloud server runs these tools with an injected client factory and workspace
provider (set_client_factory, set_workspace_provider). These tests inject the same
seams with an in-memory Cloud served over httpx.MockTransport.

A stdio session with cloud credentials reaches Cloud through
get_cloud_proxy_client, a real network client with no injection seam. The tests
for that mode replace that one function: it is the network boundary.
"""

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
import pytest
from fastmcp.exceptions import ToolError

from basic_memory.mcp import async_client, project_context
from basic_memory.mcp.async_client import set_client_factory
from basic_memory.mcp.project_context import set_workspace_provider
from basic_memory.mcp.tools import bm_project, bm_project_delete
from basic_memory.schemas.cloud import WorkspaceInfo
from basic_memory.schemas.project_info import ProjectItem, ProjectList, ProjectStatusResponse
from basic_memory.utils import generate_permalink

type FileDeleteStatus = Literal["pending", "skipped", "complete", "failed"]


def _workspace(
    tenant_id: str,
    name: str,
    slug: str,
    *,
    workspace_type: str = "personal",
    is_default: bool = False,
) -> WorkspaceInfo:
    return WorkspaceInfo(
        tenant_id=tenant_id,
        name=name,
        workspace_type=workspace_type,
        slug=slug,
        role="owner",
        is_default=is_default,
        has_active_subscription=True,
    )


def _project(
    name: str,
    external_id: str,
    *,
    path: str | None = None,
    is_default: bool = False,
    display_name: str | None = None,
    is_private: bool = False,
) -> ProjectItem:
    return ProjectItem(
        id=1,
        external_id=external_id,
        name=name,
        path=path or f"/{generate_permalink(name)}",
        is_default=is_default,
        display_name=display_name,
        is_private=is_private,
    )


PERSONAL = _workspace("personal-tenant", "Personal", "personal", is_default=True)
TEAM = _workspace("team-tenant", "Team Paul", "team-paul", workspace_type="organization")


@dataclass
class FakeCloud:
    """Workspaces and their projects, answering the project API per tenant."""

    projects: dict[str, list[ProjectItem]]
    workspaces: list[WorkspaceInfo] = field(default_factory=lambda: [PERSONAL, TEAM])
    file_delete_status: FileDeleteStatus | None = "pending"
    returns_created_project: bool = True
    requests: list[tuple[str, str, str, dict[str, str]]] = field(default_factory=list)

    def handle(self, tenant: str, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((tenant, request.method, path, dict(request.url.params)))
        projects = self.projects.setdefault(tenant, [])

        if request.method == "GET" and path == "/v2/projects/":
            listing = ProjectList(
                projects=projects,
                default_project=next((p.name for p in projects if p.is_default), None),
            )
            return httpx.Response(200, json=listing.model_dump())

        if request.method == "POST" and path == "/v2/projects/":
            name = json.loads(request.content)["name"]
            project = _project(name, f"{tenant}-{generate_permalink(name)}")
            projects.append(project)
            created = ProjectStatusResponse(
                message=f"Project '{name}' added successfully",
                status="success",
                default=False,
                new_project=project if self.returns_created_project else None,
            )
            return httpx.Response(201, json=created.model_dump())

        if request.method == "POST" and path.endswith("/index"):
            accepted = {"status": "index_started", "message": "Indexing accepted"}
            return httpx.Response(200, json={**accepted, "job_id": "index-job-1"})

        if request.method == "DELETE":
            external_id = path.removeprefix("/v2/projects/")
            project = next(p for p in projects if p.external_id == external_id)
            projects.remove(project)
            deleted = ProjectStatusResponse(
                message=f"Project '{project.name}' removed successfully",
                status="success",
                default=False,
                old_project=project,
                deletion_status="pending",
                file_delete_status=self.file_delete_status,
                job_id="28993",
            )
            return httpx.Response(200, json=deleted.model_dump())

        raise AssertionError(f"Unexpected Cloud request: {request.method} {request.url}")

    @asynccontextmanager
    async def client(self, workspace: str | None = None) -> AsyncIterator[httpx.AsyncClient]:
        # No workspace means the connection's default workspace.
        tenant = workspace or next(w.tenant_id for w in self.workspaces if w.is_default)
        transport = httpx.MockTransport(lambda request: self.handle(tenant, request))
        async with httpx.AsyncClient(transport=transport, base_url="https://cloud.test") as client:
            yield client

    async def list_workspaces(self) -> list[WorkspaceInfo]:
        return self.workspaces

    def tenant_requests(self, method: str) -> list[tuple[str, str, dict[str, str]]]:
        return [(tenant, path, params) for tenant, m, path, params in self.requests if m == method]


@pytest.fixture
def seams() -> Iterator[None]:
    """Clear the injected factory and workspace provider after each test."""
    yield
    async_client._client_factory = None
    project_context._workspace_provider = None


@pytest.fixture
def hosted(seams):
    """Run the tools as the Cloud server does."""

    def install(cloud: FakeCloud) -> FakeCloud:
        set_client_factory(cloud.client)
        set_workspace_provider(cloud.list_workspaces)
        return cloud

    return install


@pytest.fixture
def stdio_with_cloud(seams, app, config_manager, monkeypatch):
    """Run the tools as a local stdio server with cloud credentials does."""

    def install(cloud: FakeCloud) -> FakeCloud:
        config = config_manager.load_config()
        config.cloud_api_key = "bmc_test_key"
        config_manager.save_config(config)
        set_workspace_provider(cloud.list_workspaces)
        monkeypatch.setattr(async_client, "get_cloud_proxy_client", cloud.client)
        return cloud

    return install


# --- Listing in Cloud ---


@pytest.mark.asyncio
async def test_list_groups_projects_by_workspace(hosted):
    hosted(
        FakeCloud(
            projects={
                PERSONAL.tenant_id: [
                    _project("personal-main", "personal-main-uuid", is_default=True),
                    _project(
                        "private-fb83af23",
                        "private-uuid",
                        display_name="My Notes",
                        is_private=True,
                    ),
                ],
                TEAM.tenant_id: [_project("team-specs", "team-specs-uuid")],
            }
        )
    )

    result = await bm_project(command="list")

    assert isinstance(result, str)
    assert "Workspace: Personal (personal default)\n" in result
    assert "- personal-main (cloud) [personal-main-uuid]\n" in result
    assert "- My Notes (private-fb83af23) (cloud) [private-uuid]\n" in result
    assert "Workspace: Team Paul (team-paul)\n" in result
    # Team projects sync with push/pull, so they are not labelled cloud-only (#1596).
    assert "- team-specs (cloud) [team-specs-uuid]\n" in result
    assert "cloud-only" not in result


@pytest.mark.asyncio
async def test_list_json_carries_workspace_metadata(hosted):
    hosted(
        FakeCloud(
            projects={
                PERSONAL.tenant_id: [
                    _project("personal-main", "personal-main-uuid", is_default=True)
                ],
                TEAM.tenant_id: [_project("cloud-proj", "team-project-uuid")],
            }
        )
    )

    result = await bm_project(command="list", output_format="json")

    assert isinstance(result, dict)
    assert result["default_project"] == "personal-main"
    by_name = {project["name"]: project for project in result["projects"]}
    assert set(by_name) == {"personal-main", "cloud-proj"}
    assert {
        key: by_name["cloud-proj"][key]
        for key in (
            "source",
            "cloud_path",
            "local_path",
            "workspace_name",
            "workspace_type",
            "workspace_tenant_id",
            "workspace_slug",
            "workspace_is_default",
            "qualified_name",
            "sync_supported",
            "sync_reason",
            "local_usage",
        )
    } == {
        "source": "cloud",
        "cloud_path": "/cloud-proj",
        "local_path": None,
        "workspace_name": "Team Paul",
        "workspace_type": "organization",
        "workspace_tenant_id": "team-tenant",
        "workspace_slug": "team-paul",
        "workspace_is_default": False,
        "qualified_name": "team-paul/cloud-proj",
        "sync_supported": True,
        "sync_reason": None,
        "local_usage": "sync-supported",
    }


@pytest.mark.asyncio
async def test_list_surfaces_workspace_discovery_failure(hosted):
    """Cloud has no local listing to fall back on, so the failure is the answer."""
    hosted(FakeCloud(projects={}))

    async def no_user_context() -> list[WorkspaceInfo]:
        raise RuntimeError("no user context")

    set_workspace_provider(no_user_context)

    with pytest.raises(RuntimeError, match="no user context"):
        await bm_project(command="list")


@pytest.mark.asyncio
@pytest.mark.parametrize("constraint", ["team-paul/main", "main", "team-main-uuid"])
async def test_constrained_json_matches_every_project_spelling(hosted, monkeypatch, constraint):
    """A workspace-qualified or external_id constraint keeps its row (#1895 review)."""
    hosted(
        FakeCloud(
            projects={
                PERSONAL.tenant_id: [],
                TEAM.tenant_id: [
                    _project("main", "team-main-uuid"),
                    _project("other", "team-other-uuid"),
                ],
            }
        )
    )
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", constraint)

    result = await bm_project(command="list", output_format="json")
    text = await bm_project(command="list")

    assert isinstance(result, dict)
    assert [row["external_id"] for row in result["projects"]] == ["team-main-uuid"]
    assert text == (
        f"Project: {constraint}\n\n"
        "Note: This MCP server is constrained to a single project.\n"
        "All operations will automatically use this project."
    )


# --- Creating in Cloud ---


@pytest.mark.asyncio
async def test_create_in_a_workspace_named_by_slug(hosted):
    """The slug resolves to the tenant id, and Cloud indexes in the background."""
    cloud = hosted(FakeCloud(projects={}))

    created = await bm_project(
        command="create",
        name="WS Project",
        path="ws-project",
        workspace="team-paul",
        output_format="json",
    )
    created_text = await bm_project(
        command="create", name="WS Text", path="ws-text", workspace="team-paul"
    )

    assert isinstance(created, dict)
    assert created["created"] is True
    assert created["indexing"] == {
        "status": "index_started",
        "message": "Indexing accepted",
        "job_id": "index-job-1",
        "state": "accepted",
    }
    assert [p.name for p in cloud.projects[TEAM.tenant_id]] == ["WS Project", "WS Text"]
    assert {tenant for tenant, *_ in cloud.requests} == {TEAM.tenant_id}
    # Background indexing: the request does not ask Cloud to wait.
    assert all(params == {} for _, _, params in cloud.tenant_requests("POST"))
    assert isinstance(created_text, str)
    assert "• State: accepted\n" in created_text
    assert "• Job ID: index-job-1\n" in created_text
    assert "Retained content will become readable and searchable" in created_text


@pytest.mark.asyncio
async def test_create_retry_does_not_compare_hosted_paths(hosted):
    """Hosted paths are tenant-relative, so a retry spelling the path differently succeeds."""
    hosted(FakeCloud(projects={PERSONAL.tenant_id: [_project("manual", "manual-uuid")]}))

    retry = await bm_project(command="create", name="manual", path="manual", output_format="json")
    retry_text = await bm_project(command="create", name="manual", path="elsewhere")

    assert isinstance(retry, dict)
    assert (retry["already_exists"], retry["path"]) == (True, "/manual")
    assert retry["indexing"]["state"] == "accepted"
    assert isinstance(retry_text, str)
    assert "Project already existed. Retained content will become readable" in retry_text


@pytest.mark.asyncio
async def test_create_needs_the_created_project_back(hosted):
    """A create answer without the project fails before indexing anything."""
    cloud = hosted(FakeCloud(projects={}, returns_created_project=False))

    with pytest.raises(RuntimeError, match="succeeded without returning the new project"):
        await bm_project(command="create", name="Missing", path="missing")

    assert not any(path.endswith("/index") for _, path, _ in cloud.tenant_requests("POST"))


# --- Deleting in Cloud ---


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_notes", [False, True])
async def test_delete_in_a_workspace_always_deletes_files(hosted, delete_notes):
    """Cloud deletes a project's files on every delete, so the request says so (#1597)."""
    cloud = hosted(
        FakeCloud(
            projects={
                TEAM.tenant_id: [
                    _project("WS Project", "ws-uuid"),
                    _project("WS Text", "ws-text-uuid"),
                ]
            }
        )
    )

    structured = await bm_project_delete(
        name="WS Project", delete_notes=delete_notes, workspace="team-paul", output_format="json"
    )
    text = await bm_project_delete(name="ws text", delete_notes=delete_notes, workspace="team-paul")

    assert cloud.tenant_requests("DELETE") == [
        (TEAM.tenant_id, "/v2/projects/ws-uuid", {"delete_notes": "true"}),
        (TEAM.tenant_id, "/v2/projects/ws-text-uuid", {"delete_notes": "true"}),
    ]
    assert isinstance(structured, dict)
    assert {
        key: structured[key]
        for key in ("action", "name", "delete_notes", "deletion_status", "file_delete_status")
    } == {
        "action": "deleted",
        "name": "WS Project",
        "delete_notes": True,
        "deletion_status": "pending",
        "file_delete_status": "pending",
    }
    assert structured["job_id"] == "28993"
    assert isinstance(text, str)
    assert "• Path: /ws-text\n" in text
    assert "• Project deletion status: pending\n" in text
    assert "• Deletion job ID: 28993\n" in text
    assert "Note-file deletion in cloud storage was queued and is pending" in text
    assert "recovered only from a cloud snapshot" in text
    assert "remain in cloud storage" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("complete", "Note files in cloud storage were deleted along with the project"),
        ("failed", "failed; note files may remain"),
        ("skipped", "was skipped; note files remain"),
        (None, "did not report a completion status; note files may remain"),
    ],
)
async def test_delete_reports_the_file_deletion_status_cloud_gave(hosted, status, expected):
    """Only an explicit completion reports the files as deleted."""
    hosted(
        FakeCloud(
            projects={PERSONAL.tenant_id: [_project("old", "old-uuid")]},
            file_delete_status=status,
        )
    )

    text = await bm_project_delete(name="old")

    assert isinstance(text, str)
    assert expected in text
    if status != "complete":
        assert "were deleted" not in text


@pytest.mark.asyncio
async def test_unknown_workspace_is_a_refusal_in_the_requested_format(hosted):
    hosted(FakeCloud(projects={}))

    with pytest.raises(ToolError) as refused:
        await bm_project_delete(name="Any Project", workspace="nope", output_format="json")

    payload = json.loads(str(refused.value))
    assert payload["error"] == "INVALID_ARGUMENTS"
    assert "Workspace 'nope' was not found" in payload["message"]


# --- Local projects beside Cloud (stdio with credentials) ---


@pytest.mark.asyncio
async def test_list_merges_local_and_cloud_projects(
    stdio_with_cloud, test_project, tmp_path_factory
):
    local_main = await bm_project(
        command="create",
        name="main",
        path=str(tmp_path_factory.mktemp("main")),
        output_format="json",
    )
    stdio_with_cloud(
        FakeCloud(
            projects={
                PERSONAL.tenant_id: [
                    _project("main", "cloud-main-uuid"),
                    _project("basic-memory-llc", "cloud-llc-uuid"),
                ]
            }
        )
    )

    text = await bm_project(command="list")
    payload = await bm_project(command="list", output_format="json")

    assert isinstance(text, str)
    assert "- main (local+cloud) [cloud-main-uuid]\n" in text
    assert "- basic-memory-llc (cloud) [cloud-llc-uuid]\n" in text
    assert f"\nLocal projects:\n- {test_project.name} (local)" in text
    assert isinstance(local_main, dict) and isinstance(payload, dict)
    assert payload["default_project"] == test_project.name
    by_name = {project["name"]: project for project in payload["projects"]}
    # The local directory is the path a local caller works in.
    assert by_name["main"]["source"] == "local+cloud"
    assert by_name["main"]["path"] == by_name["main"]["local_path"] == local_main["path"]
    assert by_name["main"]["cloud_path"] == "/main"
    assert by_name["basic-memory-llc"]["local_path"] is None
    assert by_name["basic-memory-llc"]["qualified_name"] == "personal/basic-memory-llc"
    assert by_name[test_project.name]["source"] == "local"


@pytest.mark.asyncio
async def test_list_falls_back_to_local_projects_when_cloud_fails(stdio_with_cloud, test_project):
    stdio_with_cloud(FakeCloud(projects={}))

    async def unavailable() -> list[WorkspaceInfo]:
        raise RuntimeError("cloud unavailable")

    set_workspace_provider(unavailable)

    text = await bm_project(command="list")

    assert isinstance(text, str)
    assert f"- {test_project.name} (local)" in text
    assert "cloud)" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("project_workspace", "default_workspace", "personal_is_default", "attached"),
    [
        # A same-name team project gets no local state (#848).
        (None, None, True, "personal/main"),
        # The project's own workspace_id picks the row.
        ("team-tenant", None, True, "team-paul/main"),
        # The configured default workspace comes before Cloud's default.
        (None, "team-tenant", True, "team-paul/main"),
        # With no preference and no Cloud default, the personal workspace wins.
        (None, None, False, "personal/main"),
    ],
)
async def test_local_state_attaches_to_one_workspace_row(
    stdio_with_cloud,
    config_manager,
    tmp_path_factory,
    project_workspace,
    default_workspace,
    personal_is_default,
    attached,
):
    local_main = await bm_project(
        command="create",
        name="main",
        path=str(tmp_path_factory.mktemp("main")),
        output_format="json",
    )
    config = config_manager.load_config()
    config.projects["main"].workspace_id = project_workspace
    config.default_workspace = default_workspace
    config_manager.save_config(config)
    personal = PERSONAL.model_copy(update={"is_default": personal_is_default})
    stdio_with_cloud(
        FakeCloud(
            workspaces=[TEAM, personal],
            projects={
                PERSONAL.tenant_id: [_project("main", "personal-main-uuid")],
                TEAM.tenant_id: [_project("main", "team-main-uuid")],
            },
        )
    )

    payload = await bm_project(command="list", output_format="json")

    assert isinstance(local_main, dict) and isinstance(payload, dict)
    rows: dict[str, dict[str, Any]] = {
        project["qualified_name"]: project
        for project in payload["projects"]
        if project["qualified_name"]
    }
    assert set(rows) == {"personal/main", "team-paul/main"}
    for qualified_name, row in rows.items():
        if qualified_name == attached:
            assert (row["source"], row["path"]) == ("local+cloud", local_main["path"])
        else:
            assert (row["source"], row["local_path"]) == ("cloud", None)
            assert row["path"] == "/main"
