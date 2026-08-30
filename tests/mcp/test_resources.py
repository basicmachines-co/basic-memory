from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import import_module

import pytest
from fastmcp import Context
from fastmcp.exceptions import ResourceError
from httpx import AsyncClient

from basic_memory.mcp.prompts.ai_assistant_guide import ai_assistant_guide
from basic_memory.mcp.resources.project_info import project_info
from basic_memory.mcp.server import mcp
from basic_memory.schemas import ProjectInfoResponse
from basic_memory.schemas.project_info import ProjectItem


def test_ai_assistant_guide_exists():
    """The bundled guide should orient agents and teach progressive docs discovery."""
    guide = ai_assistant_guide()

    assert "# AI Assistant Guide" in guide
    assert "recent_activity" in guide
    assert "project_id" in guide
    assert "directory" in guide
    assert "https://docs.basicmemory.com/llms.txt" in guide
    assert "https://docs.basicmemory.com/raw/...md" in guide
    assert "https://docs.basicmemory.com/llms-full.txt" in guide
    assert "input schemas exposed by this MCP server are authoritative" in guide


@pytest.mark.asyncio
async def test_project_info_resource_is_registered():
    """Project info is addressed by canonical workspace and project permalinks."""
    assert project_info is not None
    templates = await mcp.list_resource_templates()
    registered_uris = {str(template.uri_template) for template in templates}

    assert "memory://{workspace}/{project}/info" in registered_uris
    assert "memory://{project}/info" not in registered_uris


@pytest.mark.asyncio
async def test_project_info_resource_routes_workspace_project(
    client: AsyncClient,
    test_project,
    monkeypatch: pytest.MonkeyPatch,
):
    """Workspace and project URI segments use the shared qualified-project router."""
    selected_route: str | None = None
    active_project = ProjectItem(
        id=test_project.id,
        external_id=str(test_project.external_id),
        name=test_project.name,
        path=test_project.path,
        is_default=test_project.is_default,
    )

    @asynccontextmanager
    async def project_client(
        project: str,
        context: Context | None = None,
    ) -> AsyncIterator[tuple[AsyncClient, ProjectItem]]:
        nonlocal selected_route
        selected_route = project
        yield client, active_project

    project_info_module = import_module("basic_memory.mcp.resources.project_info")
    monkeypatch.setattr(project_info_module, "get_project_client", project_client)

    info = ProjectInfoResponse.model_validate_json(
        await project_info(workspace="personal", project="test-project")
    )

    assert selected_route == "personal/test-project"
    assert info.project_name == test_project.name


@pytest.mark.asyncio
async def test_project_info_resource_routes_local_workspace(client, test_project):
    """The canonical local URI strips its workspace sentinel before local routing."""
    info = ProjectInfoResponse.model_validate_json(
        await project_info(workspace="local", project=test_project.permalink)
    )

    assert info.project_name == test_project.name


@pytest.mark.asyncio
async def test_project_info_invalid_payload_surfaces_instead_of_note_fallback(
    client, test_project, monkeypatch: pytest.MonkeyPatch
):
    """A reachable route with a broken payload is a backend fault, not a note miss."""

    class FakeResponse:
        def json(self):
            return {"bogus": True}

    async def fake_call_get(client_, url):
        return FakeResponse()

    project_info_module = import_module("basic_memory.mcp.resources.project_info")
    monkeypatch.setattr(project_info_module, "call_get", fake_call_get)

    with pytest.raises(ResourceError, match="invalid payload"):
        await project_info(workspace="local", project=test_project.permalink)
