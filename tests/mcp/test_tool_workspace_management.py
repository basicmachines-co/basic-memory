"""Tests for workspace MCP tools."""

import pytest

from basic_memory.mcp.project_context import get_available_workspaces, set_workspace_provider
from basic_memory.mcp.tools.workspaces import list_workspaces
from basic_memory.schemas.cloud import WorkspaceInfo


class _ContextState:
    def __init__(self):
        self._state: dict[str, object] = {}

    async def get_state(self, key: str):
        return self._state.get(key)

    async def set_state(self, key: str, value: object, **kwargs) -> None:
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

    result = await list_workspaces()
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

    result = await list_workspaces()
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
        await list_workspaces()


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
        cached = await context.get_state("available_workspaces")
        if cached:
            return cached
        call_count["fetches"] += 1
        await context.set_state("available_workspaces", [workspace])
        return [workspace]

    monkeypatch.setattr(
        "basic_memory.mcp.tools.workspaces.get_available_workspaces",
        fake_get_available_workspaces,
    )

    first = await list_workspaces(context=context)
    second = await list_workspaces(context=context)

    assert "# Available Workspaces (1)" in first
    assert "# Available Workspaces (1)" in second
    assert call_count["fetches"] == 1


# --- Workspace provider injection tests ---


@pytest.fixture
def _reset_workspace_provider(monkeypatch):
    """Ensure _workspace_provider is reset after each test."""
    import basic_memory.mcp.project_context as _mod

    monkeypatch.setattr(_mod, "_workspace_provider", None)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_reset_workspace_provider")
async def test_get_available_workspaces_uses_provider_when_set():
    """When a workspace provider is injected, it is called instead of the control-plane client."""
    expected = [
        WorkspaceInfo(
            tenant_id="aaaa-bbbb",
            workspace_type="personal",
            name="Injected",
            role="owner",
        ),
    ]

    async def fake_provider() -> list[WorkspaceInfo]:
        return expected

    set_workspace_provider(fake_provider)

    result = await get_available_workspaces()
    assert len(result) == 1
    assert result[0].tenant_id == "aaaa-bbbb"
    assert result[0].name == "Injected"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_reset_workspace_provider")
async def test_get_available_workspaces_falls_back_without_provider(monkeypatch):
    """Without a provider, get_available_workspaces uses the control-plane client (existing path)."""
    called = {"control_plane": False}

    async def fake_control_plane_path(context=None):
        called["control_plane"] = True
        return []

    # Patch the entire function to avoid needing real credentials
    monkeypatch.setattr(
        "basic_memory.mcp.tools.workspaces.get_available_workspaces",
        fake_control_plane_path,
    )

    result = await list_workspaces()
    assert called["control_plane"]
    assert "# No Workspaces Available" in result


@pytest.mark.asyncio
@pytest.mark.usefixtures("_reset_workspace_provider")
async def test_get_available_workspaces_provider_caches_in_context():
    """Provider results are cached in the MCP context for subsequent calls."""
    call_count = {"provider": 0}
    workspace = WorkspaceInfo(
        tenant_id="cccc-dddd",
        workspace_type="organization",
        name="Cached Provider",
        role="editor",
    )

    async def counting_provider() -> list[WorkspaceInfo]:
        call_count["provider"] += 1
        return [workspace]

    set_workspace_provider(counting_provider)
    context = _ContextState()

    # First call: provider is invoked, result cached
    first = await get_available_workspaces(context=context)
    assert len(first) == 1
    assert call_count["provider"] == 1

    # Second call: served from context cache, provider not called again
    second = await get_available_workspaces(context=context)
    assert len(second) == 1
    assert call_count["provider"] == 1
