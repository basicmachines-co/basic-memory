"""Tests for workspace MCP tools."""

import pytest

from basic_memory.mcp.tools.workspaces import list_workspaces
from basic_memory.schemas.cloud import WorkspaceInfo


class _ContextState:
    def __init__(self):
        self._state: dict[str, object] = {}

    def get_state(self, key: str):
        return self._state.get(key)

    def set_state(self, key: str, value: object) -> None:
        self._state[key] = value


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
    async def fake_get_available_workspaces(context=None):
        raise RuntimeError("Workspace discovery requires OAuth login. Run 'bm cloud login' first.")

    monkeypatch.setattr(
        "basic_memory.mcp.tools.workspaces.get_available_workspaces",
        fake_get_available_workspaces,
    )

    with pytest.raises(RuntimeError, match="Workspace discovery requires OAuth login"):
        await list_workspaces.fn()


@pytest.mark.asyncio
async def test_list_workspaces_uses_context_cache_path(monkeypatch):
    context = _ContextState()
    call_count = {"fetches": 0}
    workspace = WorkspaceInfo(
        tenant_id="33333333-3333-3333-3333-333333333333",
        workspace_type="personal",
        name="Cached",
        role="owner",
    )

    async def fake_get_available_workspaces(context=None):
        assert context is not None
        cached = context.get_state("available_workspaces")
        if cached:
            return cached
        call_count["fetches"] += 1
        context.set_state("available_workspaces", [workspace])
        return [workspace]

    monkeypatch.setattr(
        "basic_memory.mcp.tools.workspaces.get_available_workspaces",
        fake_get_available_workspaces,
    )

    first = await list_workspaces.fn(context=context)
    second = await list_workspaces.fn(context=context)

    assert "# Available Workspaces (1)" in first
    assert "# Available Workspaces (1)" in second
    assert call_count["fetches"] == 1
