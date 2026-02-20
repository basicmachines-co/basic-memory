"""Tests for project context utilities (no standard-library mock usage).

These functions are config/env driven, so we use the real ConfigManager-backed
test config file and pytest monkeypatch for environment variables.
"""

from __future__ import annotations

import pytest


class _ContextState:
    """Minimal FastMCP context-state stub for unit tests."""

    def __init__(self):
        self._state: dict[str, object] = {}

    def get_state(self, key: str):
        return self._state.get(key)

    def set_state(self, key: str, value: object) -> None:
        self._state[key] = value


@pytest.mark.asyncio
async def test_returns_none_when_no_default_and_no_project(config_manager, monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    cfg.default_project = None
    config_manager.save_config(cfg)

    monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)
    assert await resolve_project_parameter(project=None, allow_discovery=False) is None


@pytest.mark.asyncio
async def test_allows_discovery_when_enabled(config_manager):
    from basic_memory.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    cfg.default_project = None
    config_manager.save_config(cfg)

    assert await resolve_project_parameter(project=None, allow_discovery=True) is None


@pytest.mark.asyncio
async def test_returns_project_when_specified(config_manager):
    from basic_memory.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    config_manager.save_config(cfg)

    assert await resolve_project_parameter(project="my-project") == "my-project"


@pytest.mark.asyncio
async def test_uses_env_var_priority(config_manager, monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    config_manager.save_config(cfg)

    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "env-project")
    assert await resolve_project_parameter(project="explicit-project") == "env-project"


@pytest.mark.asyncio
async def test_uses_explicit_project_when_no_env(config_manager, monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    config_manager.save_config(cfg)

    monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)
    assert await resolve_project_parameter(project="explicit-project") == "explicit-project"


@pytest.mark.asyncio
async def test_uses_default_project(config_manager, config_home, monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter
    from basic_memory.config import ProjectEntry

    cfg = config_manager.load_config()
    (config_home / "default-project").mkdir(parents=True, exist_ok=True)
    cfg.projects["default-project"] = ProjectEntry(path=str(config_home / "default-project"))
    cfg.default_project = "default-project"
    config_manager.save_config(cfg)

    monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)
    assert await resolve_project_parameter(project=None) == "default-project"


@pytest.mark.asyncio
async def test_returns_none_when_no_default(config_manager, monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    cfg.default_project = None
    config_manager.save_config(cfg)

    monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)
    assert await resolve_project_parameter(project=None) is None


@pytest.mark.asyncio
async def test_env_constraint_overrides_default(config_manager, config_home, monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter
    from basic_memory.config import ProjectEntry

    cfg = config_manager.load_config()
    (config_home / "default-project").mkdir(parents=True, exist_ok=True)
    cfg.projects["default-project"] = ProjectEntry(path=str(config_home / "default-project"))
    cfg.default_project = "default-project"
    config_manager.save_config(cfg)

    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "env-project")
    assert await resolve_project_parameter(project=None) == "env-project"


@pytest.mark.asyncio
async def test_workspace_auto_selects_single_and_caches(monkeypatch):
    from basic_memory.mcp.project_context import resolve_workspace_parameter
    from basic_memory.schemas.cloud import WorkspaceInfo

    context = _ContextState()
    only_workspace = WorkspaceInfo(
        tenant_id="11111111-1111-1111-1111-111111111111",
        workspace_type="personal",
        name="Personal",
        role="owner",
    )

    async def fake_get_available_workspaces(context=None):
        return [only_workspace]

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fake_get_available_workspaces,
    )

    resolved = await resolve_workspace_parameter(context=context)
    assert resolved.tenant_id == only_workspace.tenant_id
    assert context.get_state("active_workspace") == only_workspace


@pytest.mark.asyncio
async def test_workspace_requires_user_choice_when_multiple(monkeypatch):
    from basic_memory.mcp.project_context import resolve_workspace_parameter
    from basic_memory.schemas.cloud import WorkspaceInfo

    workspaces = [
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

    async def fake_get_available_workspaces(context=None):
        return workspaces

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fake_get_available_workspaces,
    )

    with pytest.raises(ValueError, match="Multiple workspaces are available"):
        await resolve_workspace_parameter(context=_ContextState())


@pytest.mark.asyncio
async def test_workspace_explicit_selection_by_tenant_id_or_name(monkeypatch):
    from basic_memory.mcp.project_context import resolve_workspace_parameter
    from basic_memory.schemas.cloud import WorkspaceInfo

    team_workspace = WorkspaceInfo(
        tenant_id="22222222-2222-2222-2222-222222222222",
        workspace_type="organization",
        name="Team",
        role="editor",
    )
    workspaces = [
        WorkspaceInfo(
            tenant_id="11111111-1111-1111-1111-111111111111",
            workspace_type="personal",
            name="Personal",
            role="owner",
        ),
        team_workspace,
    ]

    async def fake_get_available_workspaces(context=None):
        return workspaces

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fake_get_available_workspaces,
    )

    resolved_by_id = await resolve_workspace_parameter(workspace=team_workspace.tenant_id)
    assert resolved_by_id.tenant_id == team_workspace.tenant_id

    resolved_by_name = await resolve_workspace_parameter(workspace="team")
    assert resolved_by_name.tenant_id == team_workspace.tenant_id


@pytest.mark.asyncio
async def test_workspace_invalid_selection_lists_choices(monkeypatch):
    from basic_memory.mcp.project_context import resolve_workspace_parameter
    from basic_memory.schemas.cloud import WorkspaceInfo

    workspaces = [
        WorkspaceInfo(
            tenant_id="11111111-1111-1111-1111-111111111111",
            workspace_type="personal",
            name="Personal",
            role="owner",
        )
    ]

    async def fake_get_available_workspaces(context=None):
        return workspaces

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fake_get_available_workspaces,
    )

    with pytest.raises(ValueError, match="Workspace 'missing-workspace' was not found"):
        await resolve_workspace_parameter(workspace="missing-workspace")


@pytest.mark.asyncio
async def test_workspace_uses_cached_workspace_without_fetch(monkeypatch):
    from basic_memory.mcp.project_context import resolve_workspace_parameter
    from basic_memory.schemas.cloud import WorkspaceInfo

    cached_workspace = WorkspaceInfo(
        tenant_id="11111111-1111-1111-1111-111111111111",
        workspace_type="personal",
        name="Personal",
        role="owner",
    )
    context = _ContextState()
    context.set_state("active_workspace", cached_workspace)

    async def fail_if_called(context=None):  # pragma: no cover
        raise AssertionError("Workspace fetch should not run when cache is available")

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fail_if_called,
    )

    resolved = await resolve_workspace_parameter(context=context)
    assert resolved.tenant_id == cached_workspace.tenant_id


@pytest.mark.asyncio
async def test_get_project_client_rejects_workspace_for_local_project():
    from basic_memory.mcp.project_context import get_project_client

    with pytest.raises(
        ValueError, match="Workspace 'tenant-123' cannot be used with local project"
    ):
        async with get_project_client(project="main", workspace="tenant-123"):
            pass
