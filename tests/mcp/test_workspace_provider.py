"""Workspace discovery through an injected workspace provider."""

import pytest

from basic_memory.mcp.project_context import get_available_workspaces, set_workspace_provider
from basic_memory.schemas.cloud import WorkspaceInfo
from tests.mcp.conftest import ContextState, ctx


def _workspace(
    *,
    tenant_id: str,
    workspace_type: str,
    name: str,
    role: str,
    slug: str | None = None,
    is_default: bool = False,
) -> WorkspaceInfo:
    return WorkspaceInfo(
        tenant_id=tenant_id,
        workspace_type=workspace_type,
        slug=slug or name.casefold().replace(" ", "-"),
        name=name,
        role=role,
        is_default=is_default,
    )


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
        _workspace(
            tenant_id="aaaa-bbbb",
            workspace_type="personal",
            slug="injected",
            name="Injected",
            role="owner",
            is_default=True,
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
async def test_get_available_workspaces_provider_caches_in_context():
    """Provider results are cached in the MCP context for subsequent calls."""
    call_count = {"provider": 0}
    workspace = _workspace(
        tenant_id="cccc-dddd",
        workspace_type="organization",
        slug="cached-provider",
        name="Cached Provider",
        role="editor",
    )

    async def counting_provider() -> list[WorkspaceInfo]:
        call_count["provider"] += 1
        return [workspace]

    set_workspace_provider(counting_provider)
    context = ContextState()

    # First call: provider is invoked, result cached
    first = await get_available_workspaces(context=ctx(context))
    assert len(first) == 1
    assert call_count["provider"] == 1

    # Second call: served from context cache, provider not called again
    second = await get_available_workspaces(context=ctx(context))
    assert len(second) == 1
    assert call_count["provider"] == 1
