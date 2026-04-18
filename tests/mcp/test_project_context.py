"""Tests for project context utilities (no standard-library mock usage).

These functions are config/env driven, so we use the real ConfigManager-backed
test config file and pytest monkeypatch for environment variables.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, cast

import pytest


class _ContextState:
    """Minimal FastMCP context-state stub for unit tests."""

    def __init__(self):
        self._state: dict[str, object] = {}

    async def get_state(self, key: str):
        return self._state.get(key)

    async def set_state(self, key: str, value: object, **kwargs) -> None:
        self._state[key] = value

    async def info(self, message: str) -> None:
        self._state["info_message"] = message


def _ctx(context: _ContextState) -> Any:
    return cast(Any, context)


def _workspace(
    *,
    tenant_id: str,
    workspace_type: str,
    name: str,
    role: str,
    slug: str | None = None,
    is_default: bool = False,
):
    from basic_memory.schemas.cloud import WorkspaceInfo

    return WorkspaceInfo(
        tenant_id=tenant_id,
        workspace_type=workspace_type,
        slug=slug or name.casefold().replace(" ", "-"),
        name=name,
        role=role,
        is_default=is_default,
    )


def _project(
    name: str,
    *,
    id: int = 1,
    external_id: str = "11111111-1111-1111-1111-111111111111",
    is_default: bool = False,
):
    from basic_memory.schemas.project_info import ProjectItem

    return ProjectItem(
        id=id,
        external_id=external_id,
        name=name,
        path=f"/{name}",
        is_default=is_default,
    )


@pytest.mark.asyncio
async def test_returns_none_when_no_default_and_no_project(config_manager, monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    cfg.default_project = None
    config_manager.save_config(cfg)

    monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)

    # Prevent API fallback from returning a project via stale dependency overrides
    async def _no_api_fallback():
        return None

    monkeypatch.setattr(
        "basic_memory.mcp.project_context._resolve_default_project_from_api",
        _no_api_fallback,
    )
    assert await resolve_project_parameter(project=None, allow_discovery=False) is None


@pytest.mark.asyncio
async def test_allows_discovery_when_enabled(config_manager, monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    cfg.default_project = None
    config_manager.save_config(cfg)

    # Prevent API fallback from returning a project via stale dependency overrides
    async def _no_api_fallback():
        return None

    monkeypatch.setattr(
        "basic_memory.mcp.project_context._resolve_default_project_from_api",
        _no_api_fallback,
    )
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
async def test_canonicalizes_case_insensitive_project_reference(
    config_manager, config_home, monkeypatch
):
    from basic_memory.config import ProjectEntry
    from basic_memory.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    project_name = "Personal-Project"
    project_path = config_home / "personal-project"
    project_path.mkdir(parents=True, exist_ok=True)
    cfg.projects[project_name] = ProjectEntry(path=str(project_path))
    config_manager.save_config(cfg)

    monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)

    assert await resolve_project_parameter(project="personal-project") == project_name
    assert await resolve_project_parameter(project="PERSONAL-PROJECT") == project_name


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

    # Prevent API fallback from returning a project via stale dependency overrides
    async def _no_api_fallback():
        return None

    monkeypatch.setattr(
        "basic_memory.mcp.project_context._resolve_default_project_from_api",
        _no_api_fallback,
    )
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

    context = _ContextState()
    only_workspace = _workspace(
        tenant_id="11111111-1111-1111-1111-111111111111",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
    )

    async def fake_get_available_workspaces(context=None):
        return [only_workspace]

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fake_get_available_workspaces,
    )

    resolved = await resolve_workspace_parameter(context=_ctx(context))
    assert resolved.tenant_id == only_workspace.tenant_id
    assert await context.get_state("active_workspace") == only_workspace.model_dump()


@pytest.mark.asyncio
async def test_workspace_requires_user_choice_when_multiple(monkeypatch):
    from basic_memory.mcp.project_context import resolve_workspace_parameter

    workspaces = [
        _workspace(
            tenant_id="11111111-1111-1111-1111-111111111111",
            workspace_type="personal",
            slug="personal",
            name="Personal",
            role="owner",
            is_default=True,
        ),
        _workspace(
            tenant_id="22222222-2222-2222-2222-222222222222",
            workspace_type="organization",
            slug="team",
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
        await resolve_workspace_parameter(context=_ctx(_ContextState()))


@pytest.mark.asyncio
async def test_workspace_explicit_selection_by_tenant_id_or_name(monkeypatch):
    from basic_memory.mcp.project_context import resolve_workspace_parameter

    team_workspace = _workspace(
        tenant_id="22222222-2222-2222-2222-222222222222",
        workspace_type="organization",
        slug="team",
        name="Team",
        role="editor",
    )
    workspaces = [
        _workspace(
            tenant_id="11111111-1111-1111-1111-111111111111",
            workspace_type="personal",
            slug="personal",
            name="Personal",
            role="owner",
            is_default=True,
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

    workspaces = [
        _workspace(
            tenant_id="11111111-1111-1111-1111-111111111111",
            workspace_type="personal",
            slug="personal",
            name="Personal",
            role="owner",
            is_default=True,
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

    cached_workspace = _workspace(
        tenant_id="11111111-1111-1111-1111-111111111111",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
    )
    context = _ContextState()
    await context.set_state("active_workspace", cached_workspace.model_dump())

    async def fail_if_called(context=None):  # pragma: no cover
        raise AssertionError("Workspace fetch should not run when cache is available")

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fail_if_called,
    )

    resolved = await resolve_workspace_parameter(context=_ctx(context))
    assert resolved.tenant_id == cached_workspace.tenant_id


@pytest.mark.asyncio
async def test_workspace_project_index_caches_and_invalidates(monkeypatch):
    import basic_memory.mcp.project_context as project_context
    from basic_memory.mcp.project_context import (
        WorkspaceProjectEntry,
        _ensure_workspace_project_index,
        invalidate_workspace_project_index,
    )

    context = _ContextState()
    personal = _workspace(
        tenant_id="personal-tenant",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
    )
    acme = _workspace(
        tenant_id="acme-tenant",
        workspace_type="organization",
        slug="acme",
        name="Acme",
        role="editor",
    )
    calls: list[str] = []

    async def fake_get_available_workspaces(context=None):
        return [personal, acme]

    async def fake_fetch_workspace_project_entries(workspace, context=None):
        calls.append(workspace.slug)
        project = _project(
            f"{workspace.slug}-notes",
            id=len(calls),
            external_id=f"{workspace.slug}-project-id",
        )
        return (WorkspaceProjectEntry(workspace=workspace, project=project),)

    monkeypatch.setattr(project_context, "get_available_workspaces", fake_get_available_workspaces)
    monkeypatch.setattr(
        project_context,
        "_fetch_workspace_project_entries",
        fake_fetch_workspace_project_entries,
    )

    first = await _ensure_workspace_project_index(context=_ctx(context))
    second = await _ensure_workspace_project_index(context=_ctx(context))

    assert [entry.qualified_name for entry in first.entries] == [
        "personal/personal-notes",
        "acme/acme-notes",
    ]
    assert second.entries == first.entries
    assert calls == ["personal", "acme"]

    await invalidate_workspace_project_index(_ctx(context))
    await _ensure_workspace_project_index(context=_ctx(context))
    assert calls == ["personal", "acme", "personal", "acme"]


@pytest.mark.asyncio
async def test_workspace_project_index_keeps_successes_when_workspace_fetch_fails(
    monkeypatch,
):
    import basic_memory.mcp.project_context as project_context
    from basic_memory.mcp.project_context import (
        WorkspaceProjectEntry,
        _ensure_workspace_project_index,
        resolve_workspace_project_identifier,
    )

    context = _ContextState()
    personal = _workspace(
        tenant_id="personal-tenant",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
    )
    acme = _workspace(
        tenant_id="acme-tenant",
        workspace_type="organization",
        slug="acme",
        name="Acme",
        role="editor",
    )
    project = _project("Meeting Notes", id=7, external_id="personal-meeting-notes")

    async def fake_get_available_workspaces(context=None):
        return [personal, acme]

    async def fake_fetch_workspace_project_entries(workspace, context=None):
        if workspace.slug == "acme":
            raise RuntimeError("acme unavailable")
        return (WorkspaceProjectEntry(workspace=workspace, project=project),)

    monkeypatch.setattr(project_context, "get_available_workspaces", fake_get_available_workspaces)
    monkeypatch.setattr(
        project_context,
        "_fetch_workspace_project_entries",
        fake_fetch_workspace_project_entries,
    )

    index = await _ensure_workspace_project_index(context=_ctx(context))

    assert [entry.qualified_name for entry in index.entries] == ["personal/meeting-notes"]
    assert [workspace.slug for workspace in index.failed_workspaces] == ["acme"]

    resolved = await resolve_workspace_project_identifier(
        "personal/meeting-notes",
        context=_ctx(context),
    )
    assert resolved.project.external_id == "personal-meeting-notes"

    with pytest.raises(ValueError, match="Use 'personal/meeting-notes'"):
        await resolve_workspace_project_identifier(
            "meeting-notes",
            context=_ctx(context),
        )


@pytest.mark.asyncio
async def test_workspace_project_index_raises_when_all_workspace_fetches_fail(
    monkeypatch,
):
    import basic_memory.mcp.project_context as project_context
    from basic_memory.mcp.project_context import _ensure_workspace_project_index

    personal = _workspace(
        tenant_id="personal-tenant",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
    )

    async def fake_get_available_workspaces(context=None):
        return [personal]

    async def fake_fetch_workspace_project_entries(workspace, context=None):
        raise RuntimeError("tenant unavailable")

    monkeypatch.setattr(project_context, "get_available_workspaces", fake_get_available_workspaces)
    monkeypatch.setattr(
        project_context,
        "_fetch_workspace_project_entries",
        fake_fetch_workspace_project_entries,
    )

    with pytest.raises(ValueError, match="Unable to discover projects"):
        await _ensure_workspace_project_index()


@pytest.mark.asyncio
async def test_fetch_workspace_project_entries_copies_default_project(monkeypatch):
    import basic_memory.mcp.async_client as async_client
    from basic_memory.mcp.project_context import _fetch_workspace_project_entries
    from basic_memory.schemas.project_info import ProjectList

    workspace = _workspace(
        tenant_id="personal-tenant",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
    )
    project = _project("Default Notes", id=3, external_id="default-notes-id")
    project_list = ProjectList(projects=[project], default_project="Default Notes")

    @asynccontextmanager
    async def fake_get_client(*args, **kwargs) -> AsyncIterator[object]:
        yield object()

    async def fake_list_projects(self):
        return project_list

    monkeypatch.setattr(async_client, "is_factory_mode", lambda: True)
    monkeypatch.setattr(async_client, "get_client", fake_get_client)
    monkeypatch.setattr(
        "basic_memory.mcp.clients.project.ProjectClient.list_projects",
        fake_list_projects,
    )

    entries = await _fetch_workspace_project_entries(workspace)

    assert project.is_default is False
    assert entries[0].project is not project
    assert entries[0].project.is_default is True


@pytest.mark.asyncio
async def test_resolve_workspace_project_identifier_handles_qualified_and_collisions(monkeypatch):
    import basic_memory.mcp.project_context as project_context
    from basic_memory.mcp.project_context import (
        WorkspaceProjectEntry,
        _build_workspace_project_index,
        resolve_workspace_project_identifier,
    )

    personal = _workspace(
        tenant_id="personal-tenant",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
    )
    acme = _workspace(
        tenant_id="acme-tenant",
        workspace_type="organization",
        slug="acme",
        name="Acme",
        role="editor",
    )
    entries = (
        WorkspaceProjectEntry(
            workspace=personal,
            project=_project("Meeting Notes", id=1, external_id="personal-project-id"),
        ),
        WorkspaceProjectEntry(
            workspace=acme,
            project=_project("Meeting Notes", id=2, external_id="acme-project-id"),
        ),
    )
    index = _build_workspace_project_index((personal, acme), entries)

    async def fake_index(context=None):
        return index

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fake_index)

    resolved = await resolve_workspace_project_identifier("acme/meeting-notes")
    assert resolved.workspace.slug == "acme"
    assert resolved.project.external_id == "acme-project-id"

    with pytest.raises(ValueError, match="Use: personal/meeting-notes or acme/meeting-notes"):
        await resolve_workspace_project_identifier("meeting-notes")


@pytest.mark.asyncio
async def test_resolve_workspace_project_identifier_uses_active_workspace_for_duplicate(
    monkeypatch,
):
    import basic_memory.mcp.project_context as project_context
    from basic_memory.mcp.project_context import (
        WorkspaceProjectEntry,
        _build_workspace_project_index,
        resolve_workspace_project_identifier,
    )

    context = _ContextState()
    personal = _workspace(
        tenant_id="personal-tenant",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
    )
    acme = _workspace(
        tenant_id="acme-tenant",
        workspace_type="organization",
        slug="acme",
        name="Acme",
        role="editor",
    )
    await context.set_state("active_workspace", acme.model_dump())
    entries = (
        WorkspaceProjectEntry(
            workspace=personal,
            project=_project("Meeting Notes", id=1, external_id="personal-project-id"),
        ),
        WorkspaceProjectEntry(
            workspace=acme,
            project=_project("Meeting Notes", id=2, external_id="acme-project-id"),
        ),
    )
    index = _build_workspace_project_index((personal, acme), entries)

    async def fake_index(context=None):
        return index

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fake_index)

    resolved = await resolve_workspace_project_identifier(
        "meeting-notes",
        context=_ctx(context),
    )
    assert resolved.workspace.slug == "acme"
    assert resolved.project.external_id == "acme-project-id"


@pytest.mark.asyncio
async def test_resolve_project_parameter_uses_cached_active_project_before_api_default_lookup(
    config_manager, monkeypatch
):
    from basic_memory.mcp.project_context import resolve_project_parameter
    from basic_memory.schemas.project_info import ProjectItem

    config = config_manager.load_config()
    config.default_project = None
    config_manager.save_config(config)

    context = _ContextState()
    cached_project = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="Cached Project",
        path="/tmp/cached-project",
        is_default=True,
    )
    await context.set_state("active_project", cached_project.model_dump())

    async def fail_if_called():  # pragma: no cover
        raise AssertionError("Default project API lookup should not run when project is cached")

    monkeypatch.setattr(
        "basic_memory.mcp.project_context._resolve_default_project_from_api",
        fail_if_called,
    )

    resolved = await resolve_project_parameter(project=None, context=_ctx(context))
    assert resolved == cached_project.name


@pytest.mark.asyncio
async def test_resolve_project_parameter_caches_api_default_project_name(
    config_manager, monkeypatch
):
    from basic_memory.mcp.project_context import resolve_project_parameter

    config = config_manager.load_config()
    config.default_project = None
    config_manager.save_config(config)

    context = _ContextState()
    api_calls = {"count": 0}

    async def fake_default_lookup():
        api_calls["count"] += 1
        return "cloud-default"

    monkeypatch.setattr(
        "basic_memory.mcp.project_context._resolve_default_project_from_api",
        fake_default_lookup,
    )

    first = await resolve_project_parameter(project=None, context=_ctx(context))
    second = await resolve_project_parameter(project=None, context=_ctx(context))

    assert first == "cloud-default"
    assert second == "cloud-default"
    assert api_calls["count"] == 1


@pytest.mark.asyncio
async def test_get_active_project_uses_cached_project_before_resolution(monkeypatch):
    from basic_memory.mcp.project_context import get_active_project
    from basic_memory.schemas.project_info import ProjectItem

    context = _ContextState()
    cached_project = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="Cached Project",
        path="/tmp/cached-project",
        is_default=True,
    )
    await context.set_state("active_project", cached_project.model_dump())

    async def fail_if_called(*args, **kwargs):  # pragma: no cover
        raise AssertionError("Project resolution should not run when cache matches")

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.resolve_project_parameter",
        fail_if_called,
    )

    resolved = await get_active_project(client=cast(Any, None), context=_ctx(context))
    assert resolved == cached_project


@pytest.mark.asyncio
async def test_get_active_project_uses_cached_project_for_explicit_permalink(monkeypatch):
    from basic_memory.mcp.project_context import get_active_project
    from basic_memory.schemas.project_info import ProjectItem

    context = _ContextState()
    cached_project = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="My Research",
        path="/tmp/my-research",
        is_default=False,
    )
    await context.set_state("active_project", cached_project.model_dump())

    async def fail_if_called(*args, **kwargs):  # pragma: no cover
        raise AssertionError(
            "Project resolution should not run when explicit project matches cache"
        )

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.resolve_project_parameter",
        fail_if_called,
    )

    resolved = await get_active_project(
        client=cast(Any, None), project="my-research", context=_ctx(context)
    )
    assert resolved == cached_project


@pytest.mark.asyncio
async def test_resolve_project_and_path_uses_cached_project_for_memory_url_prefix(
    config_manager, monkeypatch
):
    from basic_memory.mcp.project_context import resolve_project_and_path
    from basic_memory.schemas.project_info import ProjectItem

    config = config_manager.load_config()
    config.permalinks_include_project = False
    config_manager.save_config(config)

    context = _ContextState()
    cached_project = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="My Research",
        path="/tmp/my-research",
        is_default=False,
    )
    await context.set_state("active_project", cached_project.model_dump())

    async def fail_if_called(*args, **kwargs):  # pragma: no cover
        raise AssertionError("Project resolve API should not run when memory URL matches cache")

    async def fake_resolve_project_parameter(project=None, **kwargs):
        return cached_project.name if project else cached_project.name

    monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", fail_if_called)
    monkeypatch.setattr(
        "basic_memory.mcp.project_context.resolve_project_parameter",
        fake_resolve_project_parameter,
    )

    active_project, resolved_path, is_memory_url = await resolve_project_and_path(
        client=cast(Any, None),
        identifier="memory://my-research/notes/roadmap.md",
        context=_ctx(context),
    )

    assert active_project == cached_project
    assert resolved_path == "notes/roadmap.md"
    assert is_memory_url is True


@pytest.mark.asyncio
async def test_get_project_client_rejects_workspace_for_local_project(config_manager):
    from basic_memory.mcp.project_context import get_project_client
    from basic_memory.config import ProjectEntry

    # Register "main" as a LOCAL project so get_project_mode returns LOCAL
    config = config_manager.load_config()
    (config_manager.config_dir.parent / "main").mkdir(parents=True, exist_ok=True)
    config.projects["main"] = ProjectEntry(path=str(config_manager.config_dir.parent / "main"))
    config_manager.save_config(config)

    with pytest.raises(
        ValueError, match="Workspace 'tenant-123' cannot be used with local project"
    ):
        async with get_project_client(project="main", workspace="tenant-123"):
            pass


class TestDetectProjectFromUrlPrefix:
    """Test detect_project_from_url_prefix for URL-based project detection."""

    def test_detects_project_from_memory_url(self, config_manager):
        from basic_memory.mcp.project_context import detect_project_from_url_prefix

        config = config_manager.load_config()
        # The config has "test-project" from the conftest fixture
        result = detect_project_from_url_prefix("memory://test-project/some-note", config)
        assert result == "test-project"

    def test_detects_project_from_plain_path(self, config_manager):
        from basic_memory.mcp.project_context import detect_project_from_url_prefix

        config = config_manager.load_config()
        result = detect_project_from_url_prefix("test-project/some-note", config)
        assert result == "test-project"

    def test_returns_none_for_unknown_prefix(self, config_manager):
        from basic_memory.mcp.project_context import detect_project_from_url_prefix

        config = config_manager.load_config()
        result = detect_project_from_url_prefix("memory://unknown-project/note", config)
        assert result is None

    def test_returns_none_for_no_slash(self, config_manager):
        from basic_memory.mcp.project_context import detect_project_from_url_prefix

        config = config_manager.load_config()
        result = detect_project_from_url_prefix("memory://single-segment", config)
        assert result is None

    def test_returns_none_for_wildcard_prefix(self, config_manager):
        from basic_memory.mcp.project_context import detect_project_from_url_prefix

        config = config_manager.load_config()
        result = detect_project_from_url_prefix("memory://*/notes", config)
        assert result is None

    def test_matches_case_insensitive_via_permalink(self, config_manager):
        from basic_memory.mcp.project_context import detect_project_from_url_prefix
        from basic_memory.config import ProjectEntry

        config = config_manager.load_config()
        (config_manager.config_dir.parent / "My Research").mkdir(parents=True, exist_ok=True)
        config.projects["My Research"] = ProjectEntry(
            path=str(config_manager.config_dir.parent / "My Research")
        )
        config_manager.save_config(config)

        result = detect_project_from_url_prefix("memory://my-research/notes", config)
        assert result == "My Research"


class TestGetProjectClientRoutingOrder:
    """Test that get_project_client respects explicit routing before workspace resolution."""

    @pytest.mark.asyncio
    async def test_local_flag_skips_workspace_resolution(self, config_manager, monkeypatch):
        """--local flag should never trigger workspace resolution, even for cloud projects."""
        from basic_memory.mcp.project_context import get_project_client
        from basic_memory.config import ProjectEntry, ProjectMode

        config = config_manager.load_config()
        config.projects["cloud-proj"] = ProjectEntry(
            path=str(config_manager.config_dir.parent / "cloud-proj"),
            mode=ProjectMode.CLOUD,
        )
        config_manager.save_config(config)

        # Set explicit local routing
        monkeypatch.setenv("BASIC_MEMORY_EXPLICIT_ROUTING", "true")
        monkeypatch.setenv("BASIC_MEMORY_FORCE_LOCAL", "true")
        monkeypatch.delenv("BASIC_MEMORY_FORCE_CLOUD", raising=False)

        # Should not raise "Multiple workspaces" — it should skip workspace entirely
        # It will fail at project validation (no API running), which proves routing worked
        with pytest.raises(Exception) as exc_info:
            async with get_project_client(project="cloud-proj"):
                pass

        # The error should NOT be about workspaces
        assert "workspace" not in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_cloud_project_uses_per_project_workspace_id(self, config_manager, monkeypatch):
        """Cloud project with workspace_id in config should use it without network lookup."""
        from basic_memory.mcp.project_context import get_project_client
        from basic_memory.config import ProjectEntry, ProjectMode

        config = config_manager.load_config()
        config.projects["cloud-proj"] = ProjectEntry(
            path=str(config_manager.config_dir.parent / "cloud-proj"),
            mode=ProjectMode.CLOUD,
            workspace_id="per-project-tenant-id",
        )
        config.cloud_api_key = "bmc_test123"
        config_manager.save_config(config)

        from contextlib import asynccontextmanager
        from basic_memory.schemas.project_info import ProjectItem

        seen: dict[str, object] = {}

        async def fail_resolve_workspace_parameter(workspace=None, context=None):
            raise AssertionError("Configured workspace_id should route without workspace discovery")

        monkeypatch.setattr(
            "basic_memory.mcp.project_context.resolve_workspace_parameter",
            fail_resolve_workspace_parameter,
        )

        @asynccontextmanager
        async def fake_get_client(project_name=None, workspace=None):
            seen["project_name"] = project_name
            seen["workspace"] = workspace
            yield object()

        async def fake_get_active_project(client, project_name, context=None, headers=None):
            return ProjectItem(
                id=1,
                external_id="cloud-project-id",
                name=project_name,
                path="/cloud-proj",
                is_default=False,
            )

        monkeypatch.setattr("basic_memory.mcp.async_client.get_client", fake_get_client)
        monkeypatch.setattr(
            "basic_memory.mcp.project_context.get_active_project",
            fake_get_active_project,
        )

        async with get_project_client(project="cloud-proj") as (_client, active_project):
            assert active_project.external_id == "cloud-project-id"

        assert seen == {"project_name": "cloud-proj", "workspace": "per-project-tenant-id"}

    @pytest.mark.asyncio
    async def test_cloud_project_uses_workspace_project_index(self, config_manager, monkeypatch):
        """Cloud project without workspace_id resolves its workspace from the project index."""
        from contextlib import asynccontextmanager

        from basic_memory.mcp.project_context import get_project_client
        from basic_memory.config import ProjectEntry, ProjectMode
        from basic_memory.schemas.project_info import ProjectItem

        config = config_manager.load_config()
        config.projects["cloud-proj"] = ProjectEntry(
            path=str(config_manager.config_dir.parent / "cloud-proj"),
            mode=ProjectMode.CLOUD,
        )
        config.cloud_api_key = "bmc_test123"
        config_manager.save_config(config)

        workspace = _workspace(
            tenant_id="acme-tenant",
            workspace_type="organization",
            slug="acme",
            name="Acme",
            role="editor",
        )
        project = _project("Cloud Proj", id=42, external_id="cloud-project-id")
        seen: dict[str, object] = {}

        async def fake_resolve_workspace_project_identifier(project_name, context=None):
            from basic_memory.mcp.project_context import WorkspaceProjectEntry

            assert project_name == "cloud-proj"
            return WorkspaceProjectEntry(workspace=workspace, project=project)

        @asynccontextmanager
        async def fake_get_client(project_name=None, workspace=None):
            seen["project_name"] = project_name
            seen["workspace"] = workspace
            yield object()

        async def fake_get_active_project(client, project_name, context=None, headers=None):
            assert project_name == "Cloud Proj"
            return ProjectItem(
                id=project.id,
                external_id=project.external_id,
                name=project.name,
                path=project.path,
                is_default=False,
            )

        monkeypatch.setattr(
            "basic_memory.mcp.project_context.resolve_workspace_project_identifier",
            fake_resolve_workspace_project_identifier,
        )
        monkeypatch.setattr("basic_memory.mcp.async_client.get_client", fake_get_client)
        monkeypatch.setattr(
            "basic_memory.mcp.project_context.get_active_project", fake_get_active_project
        )

        async with get_project_client(project="cloud-proj") as (_client, active_project):
            assert active_project.external_id == "cloud-project-id"

        assert seen == {"project_name": "Cloud Proj", "workspace": "acme-tenant"}

    @pytest.mark.asyncio
    async def test_cloud_only_project_routes_to_cloud(self, config_manager, monkeypatch):
        """Project NOT in local config should route to cloud (not default to LOCAL).

        Cloud-only projects aren't registered in local config. The routing logic
        should detect this and resolve the owning workspace from the cloud index.
        """
        from contextlib import asynccontextmanager

        from basic_memory.mcp.project_context import get_project_client
        from basic_memory.schemas.project_info import ProjectItem

        config = config_manager.load_config()
        # Do NOT add "cloud-only-proj" to config.projects — it's cloud-only
        config.cloud_api_key = "bmc_test123"
        config_manager.save_config(config)

        workspace = _workspace(
            tenant_id="personal-tenant",
            workspace_type="personal",
            slug="personal",
            name="Personal",
            role="owner",
            is_default=True,
        )
        project = _project("Cloud Only Proj", id=5, external_id="cloud-only-id")
        seen: dict[str, object] = {}

        async def fake_resolve_workspace_project_identifier(project_name, context=None):
            from basic_memory.mcp.project_context import WorkspaceProjectEntry

            assert project_name == "cloud-only-proj"
            return WorkspaceProjectEntry(workspace=workspace, project=project)

        @asynccontextmanager
        async def fake_get_client(project_name=None, workspace=None):
            seen["project_name"] = project_name
            seen["workspace"] = workspace
            yield object()

        async def fake_get_active_project(client, project_name, context=None, headers=None):
            return ProjectItem(
                id=project.id,
                external_id=project.external_id,
                name=project.name,
                path=project.path,
                is_default=False,
            )

        monkeypatch.setattr(
            "basic_memory.mcp.project_context.resolve_workspace_project_identifier",
            fake_resolve_workspace_project_identifier,
        )
        monkeypatch.setattr("basic_memory.mcp.async_client.get_client", fake_get_client)
        monkeypatch.setattr(
            "basic_memory.mcp.project_context.get_active_project",
            fake_get_active_project,
        )

        async with get_project_client(project="cloud-only-proj") as (_client, active_project):
            assert active_project.external_id == "cloud-only-id"

        assert seen == {"project_name": "Cloud Only Proj", "workspace": "personal-tenant"}

    @pytest.mark.asyncio
    async def test_factory_mode_uses_workspace_index_without_control_plane(
        self, config_manager, monkeypatch
    ):
        """Factory mode resolves workspace locally and avoids control-plane HTTP.

        The cloud MCP server calls set_client_factory() so that get_client() routes
        requests through TenantASGITransport. Workspace discovery comes from the
        injected provider/index path, not from the production control-plane API.
        """
        from contextlib import asynccontextmanager

        from basic_memory.mcp import async_client
        from basic_memory.mcp.project_context import get_project_client
        from basic_memory.config import ProjectEntry, ProjectMode
        from basic_memory.schemas.project_info import ProjectItem

        config = config_manager.load_config()
        config.projects["cloud-proj"] = ProjectEntry(
            path=str(config_manager.config_dir.parent / "cloud-proj"),
            mode=ProjectMode.CLOUD,
        )
        config_manager.save_config(config)

        workspace = _workspace(
            tenant_id="team-tenant",
            workspace_type="organization",
            slug="team",
            name="Team",
            role="editor",
        )
        project = _project("Cloud Proj", id=9, external_id="factory-project-id")

        # Set up a factory (simulates what cloud MCP server does)
        @asynccontextmanager
        async def fake_factory(workspace: Any = None) -> AsyncIterator[Any]:
            assert workspace == "team-tenant"
            yield object()

        original_factory = async_client._client_factory
        async_client.set_client_factory(fake_factory)

        async def fake_resolve_workspace_project_identifier(project_name, context=None):
            from basic_memory.mcp.project_context import WorkspaceProjectEntry

            assert project_name == "cloud-proj"
            return WorkspaceProjectEntry(workspace=workspace, project=project)

        async def fake_get_active_project(client, project_name, context=None, headers=None):
            assert project_name == "Cloud Proj"
            return ProjectItem(
                id=project.id,
                external_id=project.external_id,
                name=project.name,
                path=project.path,
                is_default=False,
            )

        monkeypatch.setattr(
            "basic_memory.mcp.project_context.resolve_workspace_project_identifier",
            fake_resolve_workspace_project_identifier,
        )
        monkeypatch.setattr(
            "basic_memory.mcp.project_context.get_active_project",
            fake_get_active_project,
        )

        # Patch get_cloud_control_plane_client to fail if called
        @asynccontextmanager
        async def fail_control_plane() -> AsyncIterator[Any]:  # pragma: no cover
            raise AssertionError(
                "get_cloud_control_plane_client must not be called in factory mode"
            )
            yield

        monkeypatch.setattr(
            "basic_memory.mcp.async_client.get_cloud_control_plane_client",
            fail_control_plane,
        )

        try:
            async with get_project_client(project="cloud-proj") as (_client, active_project):
                assert active_project.external_id == "factory-project-id"
        finally:
            # Restore original factory to avoid polluting other tests
            async_client._client_factory = original_factory
