"""Integration coverage for local, cloud, and team permalink routing."""

from __future__ import annotations

import json
from collections.abc import Iterable
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastmcp import Client
from httpx import ASGITransport, AsyncClient as HttpxAsyncClient

from basic_memory.config import BasicMemoryConfig, ConfigManager, ProjectEntry
from basic_memory.mcp import async_client
from basic_memory.mcp import project_context
from basic_memory.models import Project
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.schemas.cloud import WorkspaceInfo
from basic_memory.workspace_context import workspace_permalink_headers


def _json_content(tool_result) -> Any:
    """Parse a FastMCP tool result content block into JSON."""
    assert len(tool_result.content) == 1
    assert tool_result.content[0].type == "text"
    return json.loads(tool_result.content[0].text)  # pyright: ignore [reportAttributeAccessIssue]


def _workspace(
    *,
    tenant_id: str,
    slug: str,
    workspace_type: str,
    is_default: bool = True,
) -> WorkspaceInfo:
    return WorkspaceInfo(
        tenant_id=tenant_id,
        name=slug.replace("-", " ").title(),
        workspace_type=workspace_type,
        slug=slug,
        role="owner",
        organization_id=None,
        is_default=is_default,
        has_active_subscription=True,
    )


@pytest.fixture
def personal_workspace() -> WorkspaceInfo:
    return _workspace(
        tenant_id="personal-tenant",
        slug="personal",
        workspace_type="personal",
    )


@pytest.fixture
def team_workspace() -> WorkspaceInfo:
    return _workspace(
        tenant_id="team-tenant",
        slug="team-paul",
        workspace_type="organization",
        is_default=False,
    )


@pytest.fixture
def route_workspaces(app):
    @contextmanager
    def route(*workspaces: WorkspaceInfo, forward_permalink_headers: bool = True):
        with _workspace_routing(
            app,
            workspaces,
            forward_permalink_headers=forward_permalink_headers,
        ):
            yield

    return route


@pytest_asyncio.fixture
async def alternate_project(
    config_home,
    engine_factory,
    app_config: BasicMemoryConfig,
    config_manager: ConfigManager,
) -> Project:
    """Create a second project so no-project lookups cannot pass via the default."""
    alternate_path = Path(config_home) / "alternate-project"
    alternate_path.mkdir(parents=True, exist_ok=True)

    _, session_maker = engine_factory
    project_repository = ProjectRepository(session_maker)
    project = await project_repository.create(
        {
            "name": "alternate-project",
            "description": "Non-default project for prefix routing tests",
            "path": str(alternate_path),
            "is_active": True,
            "is_default": False,
        }
    )

    app_config.projects[project.name] = ProjectEntry(path=str(alternate_path))
    config_manager.save_config(app_config)
    return project


def _save_permalink_config(
    app_config: BasicMemoryConfig,
    *,
    include_project: bool,
    default_project: str | None,
) -> None:
    app_config.permalinks_include_project = include_project
    app_config.default_project = default_project
    ConfigManager().save_config(app_config)


@contextmanager
def _workspace_routing(
    app,
    workspaces: Iterable[WorkspaceInfo],
    *,
    forward_permalink_headers: bool = True,
):
    """Route MCP tool HTTP calls through an ASGI-backed cloud workspace seam."""
    workspace_list = tuple(workspaces)
    workspace_ids = {workspace.tenant_id for workspace in workspace_list}

    async def workspace_provider():
        return list(workspace_list)

    @asynccontextmanager
    async def factory(workspace: str | None = None):
        assert workspace is None or workspace in workspace_ids
        headers = workspace_permalink_headers() if forward_permalink_headers else {}
        async with HttpxAsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers=headers,
        ) as inner:
            yield inner

    original_factory = async_client._client_factory
    original_workspace_provider = project_context._workspace_provider
    async_client.set_client_factory(factory)
    project_context.set_workspace_provider(workspace_provider)
    try:
        yield
    finally:
        async_client._client_factory = original_factory
        project_context._workspace_provider = original_workspace_provider


async def _call_json(mcp_server, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with Client(mcp_server) as client:
        result = await client.call_tool(tool_name, arguments)

    payload = _json_content(result)
    assert isinstance(payload, dict)
    return payload


async def _write_json(
    mcp_server,
    *,
    project: str,
    title: str,
    directory: str = "permalink-suite",
) -> dict[str, Any]:
    return await _call_json(
        mcp_server,
        "write_note",
        {
            "project": project,
            "title": title,
            "directory": directory,
            "content": f"# {title}\n\nUnique body for {title}.",
            "output_format": "json",
        },
    )


async def _read_json(
    mcp_server,
    *,
    identifier: str,
    project: str | None = None,
) -> dict[str, Any]:
    arguments = {
        "identifier": identifier,
        "output_format": "json",
    }
    if project is not None:
        arguments["project"] = project
    return await _call_json(mcp_server, "read_note", arguments)


async def _search_single_permalink(
    mcp_server,
    *,
    title: str,
    project: str | None = None,
) -> str:
    arguments = {
        "query": title,
        "search_type": "text",
        "output_format": "json",
    }
    if project is not None:
        arguments["project"] = project

    payload = await _call_json(mcp_server, "search_notes", arguments)
    matching_results = [
        item for item in payload["results"] if isinstance(item, dict) and item.get("title") == title
    ]
    assert len(matching_results) == 1
    permalink = matching_results[0].get("permalink")
    assert isinstance(permalink, str)
    return permalink


async def _search_permalink_exact(
    mcp_server,
    *,
    permalink: str,
    title: str,
    project: str | None = None,
) -> str:
    arguments = {
        "query": permalink,
        "search_type": "permalink",
        "output_format": "json",
    }
    if project is not None:
        arguments["project"] = project

    payload = await _call_json(mcp_server, "search_notes", arguments)
    matching_results = [
        item for item in payload["results"] if isinstance(item, dict) and item.get("title") == title
    ]
    assert len(matching_results) == 1
    result_permalink = matching_results[0].get("permalink")
    assert isinstance(result_permalink, str)
    return result_permalink


@pytest.mark.asyncio
async def test_local_short_permalink_round_trips_when_project_is_supplied(
    mcp_server,
    test_project,
    app_config,
):
    """Local short IDs need the project argument for write/search/read routing."""
    _save_permalink_config(app_config, include_project=False, default_project=None)

    title = "Local Short Permalink"
    expected_permalink = "permalink-suite/local-short-permalink"

    write_payload = await _write_json(mcp_server, project=test_project.name, title=title)
    assert write_payload["permalink"] == expected_permalink

    assert (
        await _search_single_permalink(mcp_server, project=test_project.name, title=title)
        == expected_permalink
    )

    short_read = await _read_json(
        mcp_server,
        project=test_project.name,
        identifier=expected_permalink,
    )
    assert short_read["permalink"] == expected_permalink


@pytest.mark.asyncio
async def test_local_project_permalink_routes_without_project_argument(
    mcp_server,
    alternate_project,
    app_config,
):
    """Local project-qualified IDs carry enough route context for search/read."""
    _save_permalink_config(app_config, include_project=True, default_project=None)

    title = "Local Project Permalink"
    short_permalink = "permalink-suite/local-project-permalink"
    expected_permalink = f"{alternate_project.name}/{short_permalink}"

    write_payload = await _write_json(mcp_server, project=alternate_project.name, title=title)
    assert write_payload["permalink"] == expected_permalink

    assert (
        await _search_permalink_exact(
            mcp_server,
            permalink=expected_permalink,
            title=title,
        )
        == expected_permalink
    )

    project_read = await _read_json(mcp_server, identifier=expected_permalink)
    assert project_read["permalink"] == expected_permalink


@pytest.mark.asyncio
async def test_personal_cloud_short_permalink_round_trips_when_project_is_supplied(
    mcp_server,
    test_project,
    app_config,
    personal_workspace,
    route_workspaces,
):
    """Legacy short IDs remain readable/searchable in a personal cloud workspace."""
    _save_permalink_config(app_config, include_project=False, default_project=None)

    title = "Personal Cloud Short Permalink"
    expected_permalink = "permalink-suite/personal-cloud-short-permalink"

    write_payload = await _write_json(mcp_server, project=test_project.name, title=title)
    assert write_payload["permalink"] == expected_permalink

    with route_workspaces(personal_workspace):
        assert (
            await _search_single_permalink(
                mcp_server,
                project=test_project.name,
                title=title,
            )
            == expected_permalink
        )

        short_read = await _read_json(
            mcp_server,
            project=test_project.name,
            identifier=expected_permalink,
        )
        assert short_read["permalink"] == expected_permalink


@pytest.mark.asyncio
async def test_personal_cloud_project_permalink_routes_without_project_argument(
    mcp_server,
    alternate_project,
    app_config,
    personal_workspace,
    route_workspaces,
):
    """Legacy project-qualified cloud IDs carry enough route context."""
    _save_permalink_config(app_config, include_project=True, default_project=None)

    title = "Personal Cloud Project Permalink"
    short_permalink = "permalink-suite/personal-cloud-project-permalink"
    expected_permalink = f"{alternate_project.name}/{short_permalink}"

    write_payload = await _write_json(mcp_server, project=alternate_project.name, title=title)
    assert write_payload["permalink"] == expected_permalink

    with route_workspaces(personal_workspace):
        assert (
            await _search_permalink_exact(
                mcp_server,
                permalink=expected_permalink,
                title=title,
            )
            == expected_permalink
        )

        project_read = await _read_json(mcp_server, identifier=expected_permalink)
        assert project_read["permalink"] == expected_permalink


@pytest.mark.asyncio
async def test_team_short_permalink_round_trips_when_project_is_supplied(
    mcp_server,
    test_project,
    app_config,
    team_workspace,
    route_workspaces,
):
    """Team short IDs need the qualified project argument for search/read routing."""
    team_project = f"{team_workspace.slug}/{test_project.name}"

    _save_permalink_config(app_config, include_project=False, default_project=None)
    title = "Team Short Permalink"
    expected_permalink = "permalink-suite/team-short-permalink"

    write_payload = await _write_json(mcp_server, project=test_project.name, title=title)
    assert write_payload["permalink"] == expected_permalink

    with route_workspaces(team_workspace):
        assert (
            await _search_single_permalink(
                mcp_server,
                project=team_project,
                title=title,
            )
            == expected_permalink
        )
        short_read = await _read_json(
            mcp_server,
            project=team_project,
            identifier=expected_permalink,
        )
        assert short_read["permalink"] == expected_permalink


@pytest.mark.asyncio
async def test_team_project_permalink_routes_without_project_argument(
    mcp_server,
    alternate_project,
    app_config,
    team_workspace,
    route_workspaces,
):
    """Team project-qualified IDs carry enough route context for search/read."""
    _save_permalink_config(app_config, include_project=True, default_project=None)

    title = "Team Project Permalink"
    short_permalink = "permalink-suite/team-project-permalink"
    expected_permalink = f"{alternate_project.name}/{short_permalink}"

    write_payload = await _write_json(mcp_server, project=alternate_project.name, title=title)
    assert write_payload["permalink"] == expected_permalink

    with route_workspaces(team_workspace):
        assert (
            await _search_permalink_exact(
                mcp_server,
                permalink=expected_permalink,
                title=title,
            )
            == expected_permalink
        )
        project_read = await _read_json(mcp_server, identifier=expected_permalink)
        assert project_read["permalink"] == expected_permalink


@pytest.mark.asyncio
async def test_team_workspace_permalink_routes_to_specific_workspace(
    mcp_server,
    test_project,
    app_config,
    personal_workspace,
    team_workspace,
    route_workspaces,
):
    """Workspace-qualified IDs route to the project in that workspace."""
    _save_permalink_config(app_config, include_project=True, default_project=None)
    team_project = f"{team_workspace.slug}/{test_project.name}"

    title = "Team Workspace Permalink"
    short_permalink = "permalink-suite/team-workspace-permalink"
    expected_permalink = f"{team_workspace.slug}/{test_project.name}/{short_permalink}"

    with route_workspaces(personal_workspace, team_workspace):
        write_payload = await _write_json(mcp_server, project=team_project, title=title)
        assert write_payload["permalink"] == expected_permalink

        assert (
            await _search_permalink_exact(
                mcp_server,
                permalink=expected_permalink,
                title=title,
            )
            == expected_permalink
        )

        workspace_read = await _read_json(mcp_server, identifier=expected_permalink)
        assert workspace_read["permalink"] == expected_permalink


@pytest.mark.asyncio
async def test_write_note_by_project_id_qualifies_permalink_when_headers_not_forwarded(
    mcp_server,
    test_project,
    app_config,
    team_workspace,
    route_workspaces,
):
    """MCP writes should return self-routing IDs even if the API omits slug headers."""
    _save_permalink_config(app_config, include_project=True, default_project=None)

    title = "Project Id Workspace Permalink"
    short_permalink = "permalink-suite/project-id-workspace-permalink"
    expected_permalink = f"{team_workspace.slug}/{test_project.name}/{short_permalink}"

    with route_workspaces(team_workspace, forward_permalink_headers=False):
        write_payload = await _call_json(
            mcp_server,
            "write_note",
            {
                "project_id": test_project.external_id,
                "title": title,
                "directory": "permalink-suite",
                "content": f"# {title}\n\nProject ID workspace body.",
                "output_format": "json",
            },
        )
        assert write_payload["permalink"] == expected_permalink

        workspace_read = await _read_json(
            mcp_server,
            identifier=f"memory://{write_payload['permalink']}",
        )
        assert workspace_read["title"] == title
