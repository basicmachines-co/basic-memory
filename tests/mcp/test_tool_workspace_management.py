"""Tests for workspace MCP tools."""

import pytest

from basic_memory.mcp.tools.workspaces import list_workspaces
from basic_memory.schemas.cloud import WorkspaceInfo


@pytest.mark.asyncio
async def test_list_workspaces_formats_workspace_rows(monkeypatch):
    async def fake_get_available_workspaces(context=None):
        return [
            WorkspaceInfo(
                tenant_id="11111111-1111-1111-1111-111111111111",
                workspace_type="personal",
                name="Personal",
                role="owner",
            ),
            WorkspaceInfo(
                tenant_id="22222222-2222-2222-2222-222222222222",
                workspace_type="organization",
                name="Team",
                role="editor",
            ),
        ]

    monkeypatch.setattr(
        "basic_memory.mcp.tools.workspaces.get_available_workspaces",
        fake_get_available_workspaces,
    )

    result = await list_workspaces.fn()
    assert "# Available Workspaces (2)" in result
    assert "Personal (type=personal, role=owner" in result
    assert "Team (type=organization, role=editor" in result


@pytest.mark.asyncio
async def test_list_workspaces_handles_empty_list(monkeypatch):
    async def fake_get_available_workspaces(context=None):
        return []

    monkeypatch.setattr(
        "basic_memory.mcp.tools.workspaces.get_available_workspaces",
        fake_get_available_workspaces,
    )

    result = await list_workspaces.fn()
    assert "# No Workspaces Available" in result


@pytest.mark.asyncio
async def test_list_workspaces_oauth_error_bubbles_up(monkeypatch):
    async def fake_get_available_workspaces(context=None):  # pragma: no cover
        raise RuntimeError("Workspace discovery requires OAuth login. Run 'bm cloud login' first.")

    monkeypatch.setattr(
        "basic_memory.mcp.tools.workspaces.get_available_workspaces",
        fake_get_available_workspaces,
    )

    with pytest.raises(RuntimeError, match="Workspace discovery requires OAuth login"):
        await list_workspaces.fn()
