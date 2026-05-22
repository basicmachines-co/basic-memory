"""Tests for project context utilities (no standard-library mock usage).

These functions are config/env driven, so we use the real ConfigManager-backed
test config file and pytest monkeypatch for environment variables.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, cast

import pytest

from tests.mcp.conftest import ContextState, ctx


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

    context = ContextState()
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

    resolved = await resolve_workspace_parameter(context=ctx(context))
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
        await resolve_workspace_parameter(context=ctx(ContextState()))


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
async def test_workspace_ambiguous_type_selection_lists_matching_choices(monkeypatch):
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
            slug="team-alpha",
            name="Team Alpha",
            role="editor",
        ),
        _workspace(
            tenant_id="33333333-3333-3333-3333-333333333333",
            workspace_type="organization",
            slug="team-beta",
            name="Team Beta",
            role="owner",
        ),
    ]

    async def fake_get_available_workspaces(context=None):
        return workspaces

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fake_get_available_workspaces,
    )

    with pytest.raises(ValueError) as exc_info:
        await resolve_workspace_parameter(workspace="organization")

    message = str(exc_info.value)
    assert "Workspace 'organization' matches multiple workspaces" in message
    assert "workspace: team-alpha" in message
    assert "workspace: team-beta" in message
    assert "workspace: personal" not in message


@pytest.mark.asyncio
async def test_workspace_type_selection_ignores_cached_workspace_for_ambiguity(monkeypatch):
    from basic_memory.mcp.project_context import resolve_workspace_parameter

    cached_workspace = _workspace(
        tenant_id="22222222-2222-2222-2222-222222222222",
        workspace_type="organization",
        slug="team-alpha",
        name="Team Alpha",
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
        cached_workspace,
        _workspace(
            tenant_id="33333333-3333-3333-3333-333333333333",
            workspace_type="organization",
            slug="team-beta",
            name="Team Beta",
            role="owner",
        ),
    ]
    context = ContextState()
    await context.set_state("active_workspace", cached_workspace.model_dump())
    fetches = 0

    async def fake_get_available_workspaces(context=None):
        nonlocal fetches
        fetches += 1
        return workspaces

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fake_get_available_workspaces,
    )

    with pytest.raises(ValueError) as exc_info:
        await resolve_workspace_parameter(workspace="organization", context=ctx(context))

    message = str(exc_info.value)
    assert fetches == 1
    assert "Workspace 'organization' matches multiple workspaces" in message
    assert "workspace: team-alpha" in message
    assert "workspace: team-beta" in message


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
    context = ContextState()
    await context.set_state("active_workspace", cached_workspace.model_dump())

    async def fail_if_called(context=None):  # pragma: no cover
        raise AssertionError("Workspace fetch should not run when cache is available")

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_available_workspaces",
        fail_if_called,
    )

    resolved = await resolve_workspace_parameter(context=ctx(context))
    assert resolved.tenant_id == cached_workspace.tenant_id


@pytest.mark.asyncio
async def test_workspace_project_index_caches_and_invalidates(monkeypatch):
    import basic_memory.mcp.project_context as project_context
    from basic_memory.mcp.project_context import (
        WorkspaceProjectEntry,
        _ensure_workspace_project_index,
        invalidate_workspace_project_index,
    )

    context = ContextState()
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

    first = await _ensure_workspace_project_index(context=ctx(context))
    second = await _ensure_workspace_project_index(context=ctx(context))

    assert [entry.qualified_name for entry in first.entries] == [
        "personal/personal-notes",
        "acme/acme-notes",
    ]
    assert second.entries == first.entries
    assert calls == ["personal", "acme"]

    await invalidate_workspace_project_index(ctx(context))
    await _ensure_workspace_project_index(context=ctx(context))
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

    context = ContextState()
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

    index = await _ensure_workspace_project_index(context=ctx(context))

    assert [entry.qualified_name for entry in index.entries] == ["personal/meeting-notes"]
    assert [workspace.slug for workspace in index.failed_workspaces] == ["acme"]

    resolved = await resolve_workspace_project_identifier(
        "personal/meeting-notes",
        context=ctx(context),
    )
    assert resolved.project.external_id == "personal-meeting-notes"

    with pytest.raises(ValueError, match="Use 'personal/meeting-notes'"):
        await resolve_workspace_project_identifier(
            "meeting-notes",
            context=ctx(context),
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

    # Ambiguous name resolves to the default workspace (personal)
    resolved = await resolve_workspace_project_identifier("meeting-notes")
    assert resolved.workspace.slug == "personal"
    assert resolved.project.external_id == "personal-project-id"


@pytest.mark.asyncio
async def test_detect_project_from_memory_url_prefix_resolves_workspace_slug(monkeypatch):
    import basic_memory.mcp.project_context as project_context
    from basic_memory.config import BasicMemoryConfig
    from basic_memory.mcp.project_context import (
        WorkspaceProjectEntry,
        _build_workspace_project_index,
        detect_project_from_memory_url_prefix,
    )

    personal = _workspace(
        tenant_id="personal-tenant",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
    )
    team = _workspace(
        tenant_id="team-tenant",
        workspace_type="organization",
        slug="team-paul",
        name="Team Paul",
        role="editor",
    )
    entries = (
        WorkspaceProjectEntry(
            workspace=personal,
            project=_project("main", id=1, external_id="personal-main-id"),
        ),
        WorkspaceProjectEntry(
            workspace=team,
            project=_project("main", id=2, external_id="team-main-id"),
        ),
    )
    index = _build_workspace_project_index((personal, team), entries)

    async def fake_index(context=None):
        return index

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fake_index)
    monkeypatch.setattr("basic_memory.mcp.async_client.is_factory_mode", lambda: True)

    resolved = await detect_project_from_memory_url_prefix(
        "memory://team-paul/main/notes/foo",
        BasicMemoryConfig(projects={}),
    )

    assert resolved == "team-paul/main"


@pytest.mark.asyncio
async def test_detect_project_from_memory_url_prefix_prefers_local_project_prefix(
    monkeypatch,
):
    import basic_memory.mcp.project_context as project_context
    from basic_memory.config import BasicMemoryConfig, ProjectEntry
    from basic_memory.mcp.project_context import detect_project_from_memory_url_prefix

    async def fail_if_called(context=None):  # pragma: no cover
        raise AssertionError("Local project-prefixed memory URLs must not discover workspaces")

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fail_if_called)

    resolved = await detect_project_from_memory_url_prefix(
        "memory://main/notes/foo",
        BasicMemoryConfig(
            projects={"main": ProjectEntry(path="/tmp/main")},
            cloud_api_key="bmc_test123",
        ),
    )

    assert resolved == "main"


@pytest.mark.asyncio
async def test_detect_project_from_memory_url_prefix_skips_workspace_discovery_for_local_config(
    monkeypatch,
):
    import basic_memory.mcp.project_context as project_context
    from basic_memory.config import BasicMemoryConfig, ProjectEntry
    from basic_memory.mcp.project_context import detect_project_from_memory_url_prefix

    async def fail_if_called(context=None):  # pragma: no cover
        raise AssertionError("Saved cloud credentials must not force local workspace discovery")

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fail_if_called)

    # Keep this at two path segments. Three-segment memory URLs are valid
    # workspace-qualified candidates in mixed local+cloud mode, so they should
    # attempt workspace discovery even when a local project is configured.
    resolved = await detect_project_from_memory_url_prefix(
        "memory://notes/foo",
        BasicMemoryConfig(
            projects={"main": ProjectEntry(path="/tmp/main")},
            cloud_api_key="bmc_test123",
        ),
    )

    assert resolved is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identifier", "message"),
    [
        ("memory://docs/topic/note", "No accessible workspaces found for this account."),
        (
            "docs/topic/note",
            "Unable to discover projects in any accessible workspace. Failed workspaces: personal",
        ),
    ],
)
async def test_detect_project_from_identifier_prefix_ignores_workspace_discovery_failures(
    monkeypatch,
    identifier,
    message,
):
    import basic_memory.mcp.project_context as project_context
    from basic_memory.config import BasicMemoryConfig, ProjectEntry
    from basic_memory.mcp.project_context import detect_project_from_identifier_prefix

    async def fail_workspace_index(context=None):
        raise ValueError(message)

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fail_workspace_index)

    resolved = await detect_project_from_identifier_prefix(
        identifier,
        BasicMemoryConfig(
            projects={"main": ProjectEntry(path="/tmp/main")},
            cloud_api_key="bmc_test123",
        ),
    )

    assert resolved is None


@pytest.mark.asyncio
async def test_detect_project_from_identifier_prefix_resolves_workspace_with_local_config(
    monkeypatch,
):
    import basic_memory.mcp.project_context as project_context
    from basic_memory.config import BasicMemoryConfig, ProjectEntry
    from basic_memory.mcp.project_context import (
        WorkspaceProjectEntry,
        _build_workspace_project_index,
        detect_project_from_identifier_prefix,
    )

    personal = _workspace(
        tenant_id="personal-tenant",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
    )
    index = _build_workspace_project_index(
        (personal,),
        (
            WorkspaceProjectEntry(
                workspace=personal,
                project=_project("main", id=1, external_id="personal-main-id"),
            ),
        ),
    )

    async def fake_index(context=None):
        return index

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fake_index)
    monkeypatch.setattr("basic_memory.mcp.async_client.is_factory_mode", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.async_client._explicit_routing", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.async_client._force_local_mode", lambda: False)

    config = BasicMemoryConfig(
        projects={"hermes-memory": ProjectEntry(path="/tmp/hermes-memory")},
        cloud_api_key="bmc_test123",
    )

    assert (
        await detect_project_from_identifier_prefix(
            "memory://personal/main/main-to-do-list",
            config,
        )
        == "personal/main"
    )
    assert (
        await detect_project_from_identifier_prefix(
            "personal/main/main-to-do-list",
            config,
        )
        == "personal/main"
    )


@pytest.mark.asyncio
async def test_resolve_workspace_qualified_memory_url_ignores_workspace_project_miss(
    monkeypatch,
):
    import basic_memory.mcp.project_context as project_context
    from basic_memory.mcp.project_context import (
        WorkspaceProjectEntry,
        _build_workspace_project_index,
        resolve_workspace_qualified_memory_url,
    )

    workspace = _workspace(
        tenant_id="main-tenant",
        workspace_type="organization",
        slug="main",
        name="Main Workspace",
        role="editor",
    )
    entries = (
        WorkspaceProjectEntry(
            workspace=workspace,
            project=_project("research", id=1, external_id="research-project-id"),
        ),
    )
    index = _build_workspace_project_index((workspace,), entries)

    async def fake_index(context=None):
        return index

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fake_index)

    resolved = await resolve_workspace_qualified_memory_url("memory://main/notes/foo")

    assert resolved is None


@pytest.mark.asyncio
async def test_resolve_workspace_qualified_memory_url_fails_on_duplicate_project_permalink(
    monkeypatch,
):
    import basic_memory.mcp.project_context as project_context
    from basic_memory.mcp.project_context import (
        WorkspaceProjectEntry,
        _build_workspace_project_index,
        resolve_workspace_qualified_memory_url,
    )

    team = _workspace(
        tenant_id="team-tenant",
        workspace_type="organization",
        slug="team-paul",
        name="Team Paul",
        role="editor",
    )
    entries = (
        WorkspaceProjectEntry(
            workspace=team,
            project=_project("main", id=1, external_id="team-main-id-1"),
        ),
        WorkspaceProjectEntry(
            workspace=team,
            project=_project("Main", id=2, external_id="team-main-id-2"),
        ),
    )
    index = _build_workspace_project_index((team,), entries)

    async def fake_index(context=None):
        return index

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fake_index)

    with pytest.raises(ValueError, match="matched multiple projects"):
        await resolve_workspace_qualified_memory_url("memory://team-paul/main/notes/foo")


@pytest.mark.asyncio
async def test_resolve_workspace_qualified_memory_url_uses_personal_canonical_path(
    config_manager,
    monkeypatch,
):
    import basic_memory.mcp.project_context as project_context
    from basic_memory.mcp.project_context import (
        WorkspaceProjectEntry,
        _build_workspace_project_index,
        resolve_workspace_qualified_memory_url,
    )

    config = config_manager.load_config()
    config.permalinks_include_project = True
    config_manager.save_config(config)

    personal = _workspace(
        tenant_id="personal-tenant",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
    )
    entries = (
        WorkspaceProjectEntry(
            workspace=personal,
            project=_project("main", id=1, external_id="personal-main-id"),
        ),
    )
    index = _build_workspace_project_index((personal,), entries)

    async def fake_index(context=None):
        return index

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fake_index)

    resolved = await resolve_workspace_qualified_memory_url("memory://personal/main/notes/foo")

    assert resolved is not None
    assert resolved.canonical_path == "personal/main/notes/foo"


@pytest.mark.asyncio
async def test_resolve_workspace_qualified_memory_url_keeps_org_canonical_path_without_project_prefix(
    config_manager,
    monkeypatch,
):
    import basic_memory.mcp.project_context as project_context
    from basic_memory.mcp.project_context import (
        WorkspaceProjectEntry,
        _build_workspace_project_index,
        resolve_workspace_qualified_memory_url,
    )

    config = config_manager.load_config()
    config.permalinks_include_project = False
    config_manager.save_config(config)

    team = _workspace(
        tenant_id="team-tenant",
        workspace_type="organization",
        slug="team-paul",
        name="Team Paul",
        role="editor",
    )
    entries = (
        WorkspaceProjectEntry(
            workspace=team,
            project=_project("main", id=1, external_id="team-main-id"),
        ),
    )
    index = _build_workspace_project_index((team,), entries)

    async def fake_index(context=None):
        return index

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fake_index)

    resolved = await resolve_workspace_qualified_memory_url("memory://team-paul/main/notes/foo")

    assert resolved is not None
    assert resolved.canonical_path == "team-paul/main/notes/foo"


@pytest.mark.asyncio
async def test_get_project_client_routes_duplicate_project_through_workspace_slug(
    config_manager,
    monkeypatch,
):
    from basic_memory.mcp.project_context import (
        WorkspaceProjectEntry,
        get_project_client,
    )

    config = config_manager.load_config()
    config.projects = {}
    config.cloud_api_key = "bmc_test123"
    config_manager.save_config(config)

    _workspace(
        tenant_id="personal-tenant",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
    )
    team = _workspace(
        tenant_id="team-tenant",
        workspace_type="organization",
        slug="team-paul",
        name="Team Paul",
        role="editor",
    )
    team_project = _project("main", id=2, external_id="team-main-id")
    seen: dict[str, object] = {}

    async def fake_resolve_workspace_project_identifier(project_name, context=None):
        assert project_name == "team-paul/main"
        return WorkspaceProjectEntry(workspace=team, project=team_project)

    @asynccontextmanager
    async def fake_get_client(project_name=None, workspace=None):
        seen["project_name"] = project_name
        seen["workspace"] = workspace
        yield object()

    async def fake_get_active_project(client, project_name, context=None, headers=None):
        return _project(project_name, id=team_project.id, external_id=team_project.external_id)

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.resolve_workspace_project_identifier",
        fake_resolve_workspace_project_identifier,
    )
    monkeypatch.setattr("basic_memory.mcp.async_client.get_client", fake_get_client)
    monkeypatch.setattr("basic_memory.mcp.async_client.is_factory_mode", lambda: True)
    monkeypatch.setattr(
        "basic_memory.mcp.project_context.get_active_project",
        fake_get_active_project,
    )

    async with get_project_client(project="team-paul/main") as (_client, active_project):
        assert active_project.external_id == "team-main-id"

    assert seen == {"project_name": "main", "workspace": "team-tenant"}


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

    context = ContextState()
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
        context=ctx(context),
    )
    assert resolved.workspace.slug == "acme"
    assert resolved.project.external_id == "acme-project-id"


@pytest.mark.asyncio
async def test_resolve_workspace_project_identifier_resolves_by_external_id(monkeypatch):
    """Direct lookup by external_id (UUID) bypasses name/permalink resolution."""
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
    # Same project name in two workspaces — UUID lookup must pick the right one
    # without falling back to default-workspace ambiguity handling.
    entries = (
        WorkspaceProjectEntry(
            workspace=personal,
            project=_project(
                "Meeting Notes", id=1, external_id="11111111-1111-1111-1111-111111111111"
            ),
        ),
        WorkspaceProjectEntry(
            workspace=acme,
            project=_project(
                "Meeting Notes", id=2, external_id="22222222-2222-2222-2222-222222222222"
            ),
        ),
    )
    index = _build_workspace_project_index((personal, acme), entries)

    async def fake_index(context=None):
        return index

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fake_index)

    resolved = await resolve_workspace_project_identifier("22222222-2222-2222-2222-222222222222")
    assert resolved.workspace.slug == "acme"
    assert resolved.project.external_id == "22222222-2222-2222-2222-222222222222"


@pytest.mark.asyncio
async def test_resolve_workspace_project_identifier_normalizes_uuid_forms(monkeypatch):
    """Uppercase, brace-wrapped, and urn:uuid forms canonicalize before lookup."""
    import basic_memory.mcp.project_context as project_context
    from basic_memory.mcp.project_context import (
        WorkspaceProjectEntry,
        _build_workspace_project_index,
        resolve_workspace_project_identifier,
    )

    workspace = _workspace(
        tenant_id="personal-tenant",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
    )
    canonical_uuid = "33333333-3333-3333-3333-333333333333"
    entries = (
        WorkspaceProjectEntry(
            workspace=workspace,
            project=_project("Meeting Notes", id=1, external_id=canonical_uuid),
        ),
    )
    index = _build_workspace_project_index((workspace,), entries)

    async def fake_index(context=None):
        return index

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fake_index)

    # Each variant should canonicalize to the same lowercase-hyphenated form
    for variant in (
        canonical_uuid.upper(),
        f"{{{canonical_uuid}}}",
        f"urn:uuid:{canonical_uuid}",
        canonical_uuid.replace("-", ""),
    ):
        resolved = await resolve_workspace_project_identifier(variant)
        assert resolved.project.external_id == canonical_uuid, (
            f"variant {variant!r} did not resolve to canonical UUID"
        )


@pytest.mark.asyncio
async def test_get_project_client_with_project_id_routes_locally_without_cloud(
    config_manager, monkeypatch
):
    """A UUID project_id in pure local mode must not trigger cloud routing.

    Regression: get_project_mode() defaults unknown identifiers to CLOUD because the
    local config keys by name. UUIDs are never registered locally, so without the
    fix below, passing project_id would falsely route through the cloud client and
    error out for users with no cloud credentials.
    """
    import basic_memory.mcp.project_context as project_context

    # Pure local mode: no cloud creds, no factory mode, no explicit cloud routing
    config = config_manager.load_config()
    config.cloud_api_key = None
    config_manager.save_config(config)

    monkeypatch.setattr(project_context, "has_cloud_credentials", lambda _config: False)

    captured: dict[str, object] = {}

    @asynccontextmanager
    async def fake_get_client(**kwargs) -> AsyncIterator[object]:
        captured["get_client_kwargs"] = kwargs
        yield object()

    async def fake_get_active_project(client, project, context, headers=None):
        captured["validated_project"] = project
        return _project("Local Project", id=99, external_id=project)

    monkeypatch.setattr("basic_memory.mcp.async_client.get_client", fake_get_client)
    monkeypatch.setattr("basic_memory.mcp.async_client.is_factory_mode", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.async_client._explicit_routing", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.async_client._force_local_mode", lambda: False)
    monkeypatch.setattr(project_context, "get_active_project", fake_get_active_project)

    canonical_uuid = "55555555-5555-5555-5555-555555555555"
    async with project_context.get_project_client(project_id=canonical_uuid) as (_, active):
        assert active.external_id == canonical_uuid

    # When project_id is set, get_client must be called WITHOUT project_name so the
    # ASGI fallback is selected (not the per-project cloud-by-default path).
    assert captured["get_client_kwargs"] == {}
    assert captured["validated_project"] == canonical_uuid


@pytest.mark.asyncio
async def test_get_project_client_with_local_project_id_routes_locally_with_cloud_credentials(
    config_manager, monkeypatch
):
    """A local-only project_id should resolve locally before cloud workspace discovery."""
    import basic_memory.mcp.project_context as project_context
    from basic_memory.config import ProjectEntry

    config = config_manager.load_config()
    config.projects["hermes-memory"] = ProjectEntry(
        path=str(config_manager.config_dir.parent / "hermes-memory")
    )
    config.cloud_api_key = "bmc_test123"
    config_manager.save_config(config)

    async def fail_index(context=None):  # pragma: no cover
        raise AssertionError("local project_id should not require cloud discovery")

    captured: dict[str, object] = {}

    @asynccontextmanager
    async def fake_get_client(**kwargs) -> AsyncIterator[object]:
        captured["get_client_kwargs"] = kwargs
        yield object()

    async def fake_get_active_project(client, project, context=None, headers=None):
        captured["validated_project"] = project
        return _project("Hermes Memory", id=99, external_id=project)

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fail_index)
    monkeypatch.setattr(project_context, "has_cloud_credentials", lambda _config: True)
    monkeypatch.setattr("basic_memory.mcp.async_client.get_client", fake_get_client)
    monkeypatch.setattr("basic_memory.mcp.async_client.is_factory_mode", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.async_client._explicit_routing", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.async_client._force_local_mode", lambda: False)
    monkeypatch.setattr(project_context, "get_active_project", fake_get_active_project)

    local_uuid = "55555555-5555-5555-5555-555555555555"
    async with project_context.get_project_client(project_id=local_uuid) as (_, active):
        assert active.external_id == local_uuid

    assert captured["get_client_kwargs"] == {}
    assert captured["validated_project"] == local_uuid


@pytest.mark.asyncio
async def test_get_project_client_with_local_project_id_clears_cached_workspace(
    config_manager, monkeypatch
):
    """Local project_id routing must not inherit a previous cloud workspace context."""
    import basic_memory.mcp.project_context as project_context
    from basic_memory.config import ProjectEntry

    config = config_manager.load_config()
    config.projects["hermes-memory"] = ProjectEntry(
        path=str(config_manager.config_dir.parent / "hermes-memory")
    )
    config.cloud_api_key = "bmc_test123"
    config_manager.save_config(config)

    personal = _workspace(
        tenant_id="personal-tenant",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
    )
    context = ContextState()
    await context.set_state("active_workspace", personal.model_dump())

    async def fail_index(context=None):  # pragma: no cover
        raise AssertionError("local project_id should not require cloud discovery")

    captured: dict[str, object] = {}

    @asynccontextmanager
    async def fake_get_client(**kwargs) -> AsyncIterator[object]:
        captured["get_client_kwargs"] = kwargs
        yield object()

    async def fake_get_active_project(client, project, context=None, headers=None):
        captured["validated_project"] = project
        return _project("Hermes Memory", id=99, external_id=project)

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fail_index)
    monkeypatch.setattr(project_context, "has_cloud_credentials", lambda _config: True)
    monkeypatch.setattr("basic_memory.mcp.async_client.get_client", fake_get_client)
    monkeypatch.setattr("basic_memory.mcp.async_client.is_factory_mode", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.async_client._explicit_routing", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.async_client._force_local_mode", lambda: False)
    monkeypatch.setattr(project_context, "get_active_project", fake_get_active_project)

    local_uuid = "55555555-5555-5555-5555-555555555555"
    async with project_context.get_project_client(
        project_id=local_uuid,
        context=ctx(context),
    ) as (_, active):
        assert active.external_id == local_uuid

    assert captured["get_client_kwargs"] == {}
    assert captured["validated_project"] == local_uuid
    assert await context.get_state("active_workspace") is None


@pytest.mark.asyncio
async def test_get_project_client_with_project_id_respects_env_constraint(
    config_manager, monkeypatch
):
    """BASIC_MEMORY_MCP_PROJECT must remain authoritative when project_id is supplied."""
    import basic_memory.mcp.project_context as project_context
    from basic_memory.config import ProjectEntry

    config = config_manager.load_config()
    config.projects["env-project"] = ProjectEntry(
        path=str(config_manager.config_dir.parent / "env-project")
    )
    config.projects["other-project"] = ProjectEntry(
        path=str(config_manager.config_dir.parent / "other-project")
    )
    config.cloud_api_key = "bmc_test123"
    config_manager.save_config(config)

    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "env-project")

    async def fail_index(context=None):  # pragma: no cover
        raise AssertionError("env-constrained local project should not use cloud discovery")

    captured: dict[str, object] = {}

    @asynccontextmanager
    async def fake_get_client(**kwargs) -> AsyncIterator[object]:
        captured["get_client_kwargs"] = kwargs
        yield object()

    async def fake_get_active_project(client, project, context=None, headers=None):
        captured["validated_project"] = project
        return _project(str(project), id=99, external_id="env-project-id")

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fail_index)
    monkeypatch.setattr(project_context, "has_cloud_credentials", lambda _config: True)
    monkeypatch.setattr("basic_memory.mcp.async_client.get_client", fake_get_client)
    monkeypatch.setattr("basic_memory.mcp.async_client.is_factory_mode", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.async_client._explicit_routing", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.async_client._force_local_mode", lambda: False)
    monkeypatch.setattr(project_context, "get_active_project", fake_get_active_project)

    requested_uuid = "55555555-5555-5555-5555-555555555555"
    async with project_context.get_project_client(project_id=requested_uuid) as (_, active):
        assert active.name == "env-project"

    assert captured["get_client_kwargs"] == {}
    assert captured["validated_project"] == "env-project"


@pytest.mark.asyncio
async def test_get_project_client_with_cloud_project_id_routes_to_workspace_with_local_config(
    config_manager, monkeypatch
):
    """Cloud project_id routing falls through to the workspace index after a local miss."""
    import basic_memory.mcp.project_context as project_context
    from basic_memory.config import ProjectEntry
    from basic_memory.mcp.project_context import (
        WorkspaceProjectEntry,
        _build_workspace_project_index,
    )
    from mcp.server.fastmcp.exceptions import ToolError

    config = config_manager.load_config()
    config.projects["hermes-memory"] = ProjectEntry(
        path=str(config_manager.config_dir.parent / "hermes-memory")
    )
    config.cloud_api_key = "bmc_test123"
    config_manager.save_config(config)

    cloud_uuid = "22222222-2222-2222-2222-222222222222"
    personal = _workspace(
        tenant_id="personal-tenant",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
    )
    cloud_project = _project("main", id=2, external_id=cloud_uuid)
    index = _build_workspace_project_index(
        (personal,),
        (WorkspaceProjectEntry(workspace=personal, project=cloud_project),),
    )

    async def fake_index(context=None):
        return index

    get_client_calls: list[dict[str, str | None]] = []
    validated_projects: list[str] = []

    @asynccontextmanager
    async def fake_get_client(project_name=None, workspace=None):
        get_client_calls.append({"project_name": project_name, "workspace": workspace})
        yield object()

    async def fake_get_active_project(client, project, context=None, headers=None):
        validated_projects.append(project)
        if project == cloud_uuid:
            raise ToolError("project not found")
        return _project(project, id=cloud_project.id, external_id=cloud_project.external_id)

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fake_index)
    monkeypatch.setattr(project_context, "has_cloud_credentials", lambda _config: True)
    monkeypatch.setattr("basic_memory.mcp.async_client.get_client", fake_get_client)
    monkeypatch.setattr("basic_memory.mcp.async_client.is_factory_mode", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.async_client._explicit_routing", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.async_client._force_local_mode", lambda: False)
    monkeypatch.setattr(project_context, "get_active_project", fake_get_active_project)

    async with project_context.get_project_client(project_id=cloud_uuid) as (_, active):
        assert active.external_id == cloud_uuid

    assert get_client_calls == [
        {"project_name": None, "workspace": None},
        {"project_name": "main", "workspace": "personal-tenant"},
    ]
    assert validated_projects == [cloud_uuid, "main"]


@pytest.mark.asyncio
async def test_get_project_client_clears_stale_cached_project_for_workspace_route(
    config_manager, monkeypatch
):
    """Workspace routes must not reuse a same-name project with a different UUID."""
    import basic_memory.mcp.project_context as project_context
    from basic_memory.mcp.project_context import WorkspaceProjectEntry
    from basic_memory.schemas.project_info import ProjectItem

    config = config_manager.load_config()
    config.cloud_api_key = "bmc_test123"
    config_manager.save_config(config)

    personal = _workspace(
        tenant_id="personal-tenant",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
    )
    expected_uuid = "22222222-2222-2222-2222-222222222222"
    expected_project = _project("main", id=2, external_id=expected_uuid)
    stale_project = ProjectItem(
        id=99,
        external_id="33333333-3333-3333-3333-333333333333",
        name="main",
        path="/tmp/stale-main",
        is_default=False,
    )
    context = ContextState()
    await context.set_state("active_workspace", personal.model_dump())
    await context.set_state("active_project", stale_project.model_dump())

    async def fake_resolve_workspace_project_identifier(project_name, context=None):
        assert project_name == "personal/main"
        return WorkspaceProjectEntry(workspace=personal, project=expected_project)

    @asynccontextmanager
    async def fake_get_client(project_name=None, workspace=None):
        assert project_name == "main"
        assert workspace == "personal-tenant"
        yield object()

    class FakeResponse:
        def json(self):
            return {
                "external_id": expected_uuid,
                "project_id": expected_project.id,
                "name": expected_project.name,
                "permalink": expected_project.permalink,
                "path": expected_project.path,
                "is_active": True,
                "is_default": False,
                "resolution_method": "permalink",
            }

    calls = {"count": 0}

    async def fake_call_post(*args, **kwargs):
        calls["count"] += 1
        return FakeResponse()

    monkeypatch.setattr(
        project_context,
        "resolve_workspace_project_identifier",
        fake_resolve_workspace_project_identifier,
    )
    monkeypatch.setattr(project_context, "has_cloud_credentials", lambda _config: True)
    monkeypatch.setattr("basic_memory.mcp.async_client.get_client", fake_get_client)
    monkeypatch.setattr("basic_memory.mcp.async_client.is_factory_mode", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.async_client._explicit_routing", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.async_client._force_local_mode", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", fake_call_post)

    async with project_context.get_project_client(
        project="personal/main",
        context=ctx(context),
    ) as (_, active):
        assert active.external_id == expected_uuid

    cached_project = await context.get_state("active_project")
    assert isinstance(cached_project, dict)
    assert cached_project["external_id"] == expected_uuid
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_get_project_client_prefers_project_id_over_project_name(monkeypatch):
    """When both project and project_id are passed, the UUID takes precedence."""
    import basic_memory.mcp.project_context as project_context

    # Capture which identifier flows into resolution, then short-circuit before
    # the rest of the routing chain runs (avoids real network calls).
    captured: dict[str, str | None] = {}
    sentinel = RuntimeError("stop after resolution")

    async def fake_resolve(project=None, *, allow_discovery=True, context=None):
        captured["project"] = project
        raise sentinel

    monkeypatch.setattr(project_context, "resolve_project_parameter", fake_resolve)

    canonical_uuid = "44444444-4444-4444-4444-444444444444"
    with pytest.raises(RuntimeError, match="stop after resolution"):
        async with project_context.get_project_client(
            project="ambiguous-name",
            project_id=canonical_uuid,
        ):
            pass

    assert captured["project"] == canonical_uuid


@pytest.mark.asyncio
async def test_resolve_project_parameter_uses_cached_active_project_before_api_default_lookup(
    config_manager, monkeypatch
):
    from basic_memory.mcp.project_context import resolve_project_parameter
    from basic_memory.schemas.project_info import ProjectItem

    config = config_manager.load_config()
    config.default_project = None
    config_manager.save_config(config)

    context = ContextState()
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

    resolved = await resolve_project_parameter(project=None, context=ctx(context))
    assert resolved == cached_project.name


@pytest.mark.asyncio
async def test_resolve_project_parameter_caches_api_default_project_name(
    config_manager, monkeypatch
):
    from basic_memory.mcp.project_context import resolve_project_parameter

    config = config_manager.load_config()
    config.default_project = None
    config_manager.save_config(config)

    context = ContextState()
    api_calls = {"count": 0}

    async def fake_default_lookup():
        api_calls["count"] += 1
        return "cloud-default"

    monkeypatch.setattr(
        "basic_memory.mcp.project_context._resolve_default_project_from_api",
        fake_default_lookup,
    )

    first = await resolve_project_parameter(project=None, context=ctx(context))
    second = await resolve_project_parameter(project=None, context=ctx(context))

    assert first == "cloud-default"
    assert second == "cloud-default"
    assert api_calls["count"] == 1


@pytest.mark.asyncio
async def test_get_active_project_uses_cached_project_before_resolution(monkeypatch):
    from basic_memory.mcp.project_context import get_active_project
    from basic_memory.schemas.project_info import ProjectItem

    context = ContextState()
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

    resolved = await get_active_project(client=cast(Any, None), context=ctx(context))
    assert resolved == cached_project


@pytest.mark.asyncio
async def test_get_active_project_uses_cached_project_for_explicit_permalink(monkeypatch):
    from basic_memory.mcp.project_context import get_active_project
    from basic_memory.schemas.project_info import ProjectItem

    context = ContextState()
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
        client=cast(Any, None), project="my-research", context=ctx(context)
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

    context = ContextState()
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
        context=ctx(context),
    )

    assert active_project == cached_project
    assert resolved_path == "notes/roadmap.md"
    assert is_memory_url is True


@pytest.mark.asyncio
async def test_resolve_project_and_path_keeps_workspace_qualified_canonical_path(
    config_manager,
    monkeypatch,
):
    from mcp.server.fastmcp.exceptions import ToolError

    from basic_memory.mcp.project_context import resolve_project_and_path
    from basic_memory.schemas.project_info import ProjectItem

    config = config_manager.load_config()
    config.permalinks_include_project = True
    config_manager.save_config(config)

    context = ContextState()
    cached_project = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="main",
        path="/tmp/main",
        is_default=False,
    )
    team_workspace = _workspace(
        tenant_id="team-tenant",
        workspace_type="organization",
        slug="team-paul",
        name="Team Paul",
        role="editor",
    )
    await context.set_state("active_project", cached_project.model_dump())
    await context.set_state("active_workspace", team_workspace.model_dump())

    async def fake_call_post(*args, **kwargs):
        raise ToolError("project not found")

    monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", fake_call_post)

    active_project, resolved_path, is_memory_url = await resolve_project_and_path(
        client=cast(Any, None),
        identifier="memory://team-paul/main/notes/foo",
        context=ctx(context),
    )

    assert active_project == cached_project
    assert resolved_path == "team-paul/main/notes/foo"
    assert is_memory_url is True


@pytest.mark.asyncio
async def test_resolve_project_and_path_preserves_personal_workspace_prefix(
    config_manager,
    monkeypatch,
):
    from mcp.server.fastmcp.exceptions import ToolError

    from basic_memory.mcp.project_context import resolve_project_and_path
    from basic_memory.schemas.project_info import ProjectItem

    config = config_manager.load_config()
    config.permalinks_include_project = True
    config_manager.save_config(config)

    context = ContextState()
    cached_project = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="main",
        path="/tmp/main",
        is_default=False,
    )
    personal_workspace = _workspace(
        tenant_id="personal-tenant",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
    )
    await context.set_state("active_project", cached_project.model_dump())
    await context.set_state("active_workspace", personal_workspace.model_dump())

    async def fake_call_post(*args, **kwargs):
        raise ToolError("project not found")

    monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", fake_call_post)

    active_project, resolved_path, is_memory_url = await resolve_project_and_path(
        client=cast(Any, None),
        identifier="memory://personal/main/notes/foo",
        context=ctx(context),
    )

    assert active_project == cached_project
    assert resolved_path == "personal/main/notes/foo"
    assert is_memory_url is True


@pytest.mark.asyncio
async def test_resolve_project_and_path_preserves_existing_project_prefixed_memory_url(
    config_manager,
):
    from basic_memory.mcp.project_context import resolve_project_and_path
    from basic_memory.schemas.project_info import ProjectItem

    config = config_manager.load_config()
    config.permalinks_include_project = True
    config_manager.save_config(config)

    context = ContextState()
    cached_project = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="main",
        path="/tmp/main",
        is_default=False,
    )
    await context.set_state("active_project", cached_project.model_dump())

    active_project, resolved_path, is_memory_url = await resolve_project_and_path(
        client=cast(Any, None),
        identifier="memory://main/notes/foo",
        context=ctx(context),
    )

    assert active_project == cached_project
    assert resolved_path == "main/notes/foo"
    assert is_memory_url is True


@pytest.mark.asyncio
async def test_resolve_project_and_path_uses_cached_workspace_for_active_route(
    config_manager,
    monkeypatch,
):
    from mcp.server.fastmcp.exceptions import ToolError

    import basic_memory.mcp.project_context as project_context
    from basic_memory.mcp.project_context import resolve_project_and_path
    from basic_memory.schemas.project_info import ProjectItem

    config = config_manager.load_config()
    config.permalinks_include_project = True
    config_manager.save_config(config)

    context = ContextState()
    cached_project = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="main",
        path="/tmp/main",
        is_default=False,
    )
    team_workspace = _workspace(
        tenant_id="team-tenant",
        workspace_type="organization",
        slug="team-paul",
        name="Team Paul",
        role="editor",
    )
    await context.set_state("active_project", cached_project.model_dump())
    await context.set_state("active_workspace", team_workspace.model_dump())

    async def fake_call_post(*args, **kwargs):
        raise ToolError("project not found")

    async def fake_get_active_project(*args, **kwargs):
        return cached_project

    monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", fake_call_post)
    monkeypatch.setattr(project_context, "get_active_project", fake_get_active_project)

    active_project, resolved_path, is_memory_url = await resolve_project_and_path(
        client=cast(Any, None),
        identifier="memory://notes/foo",
        project="main",
        context=ctx(context),
    )

    assert active_project == cached_project
    assert resolved_path == "team-paul/main/notes/foo"
    assert is_memory_url is True

    active_project, resolved_path, is_memory_url = await resolve_project_and_path(
        client=cast(Any, None),
        identifier="memory://main",
        project="main",
        context=ctx(context),
    )

    assert active_project == cached_project
    assert resolved_path == "team-paul/main"
    assert is_memory_url is True


@pytest.mark.asyncio
async def test_resolve_project_and_path_uses_workspace_context_for_project_root(
    config_manager,
    monkeypatch,
):
    import basic_memory.mcp.project_context as project_context
    from basic_memory.mcp.project_context import resolve_project_and_path
    from basic_memory.schemas.project_info import ProjectItem
    from basic_memory.workspace_context import workspace_permalink_context

    config = config_manager.load_config()
    config.permalinks_include_project = True
    config_manager.save_config(config)

    active = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="main",
        path="/tmp/main",
        is_default=False,
    )

    async def fake_get_active_project(*args, **kwargs):
        return active

    monkeypatch.setattr(project_context, "get_active_project", fake_get_active_project)

    with workspace_permalink_context(workspace_slug="team-paul", workspace_type="organization"):
        active_project, resolved_path, is_memory_url = await resolve_project_and_path(
            client=cast(Any, None),
            identifier="memory://main",
            project="main",
            context=ctx(ContextState()),
        )

    assert active_project == active
    assert resolved_path == "team-paul/main"
    assert is_memory_url is True


@pytest.mark.asyncio
async def test_resolve_project_and_path_uses_cached_workspace_for_cached_project_prefix(
    config_manager,
    monkeypatch,
):
    import basic_memory.mcp.project_context as project_context
    from basic_memory.mcp.project_context import resolve_project_and_path
    from basic_memory.schemas.project_info import ProjectItem

    config = config_manager.load_config()
    config.permalinks_include_project = True
    config_manager.save_config(config)

    context = ContextState()
    cached_project = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="main",
        path="/tmp/main",
        is_default=False,
    )
    team_workspace = _workspace(
        tenant_id="team-tenant",
        workspace_type="organization",
        slug="team-paul",
        name="Team Paul",
        role="editor",
    )
    await context.set_state("active_project", cached_project.model_dump())
    await context.set_state("active_workspace", team_workspace.model_dump())

    async def fail_call_post(*args, **kwargs):  # pragma: no cover
        raise AssertionError("Cached project prefix should not call project resolve API")

    async def fake_resolve_project_parameter(project=None, **kwargs):
        return cached_project.name if project else cached_project.name

    monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", fail_call_post)
    monkeypatch.setattr(
        project_context,
        "resolve_project_parameter",
        fake_resolve_project_parameter,
    )

    active_project, resolved_path, is_memory_url = await resolve_project_and_path(
        client=cast(Any, None),
        identifier="memory://main/notes/foo",
        context=ctx(context),
    )

    assert active_project == cached_project
    assert resolved_path == "team-paul/main/notes/foo"
    assert is_memory_url is True


@pytest.mark.asyncio
async def test_resolve_project_and_path_uses_cached_workspace_for_resolved_project_prefix(
    config_manager,
    monkeypatch,
):
    import basic_memory.mcp.project_context as project_context
    from basic_memory.mcp.project_context import resolve_project_and_path

    config = config_manager.load_config()
    config.permalinks_include_project = True
    config_manager.save_config(config)

    context = ContextState()
    team_workspace = _workspace(
        tenant_id="team-tenant",
        workspace_type="organization",
        slug="team-paul",
        name="Team Paul",
        role="editor",
    )
    await context.set_state("active_workspace", team_workspace.model_dump())

    class FakeResponse:
        def json(self):
            return {
                "external_id": "22222222-2222-2222-2222-222222222222",
                "project_id": 2,
                "name": "Research",
                "permalink": "research",
                "path": "/tmp/research",
                "is_active": True,
                "is_default": False,
                "resolution_method": "permalink",
            }

    async def fake_call_post(*args, **kwargs):
        return FakeResponse()

    async def fake_resolve_project_parameter(project=None, **kwargs):
        return "Research" if project else "Research"

    monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", fake_call_post)
    monkeypatch.setattr(
        project_context,
        "resolve_project_parameter",
        fake_resolve_project_parameter,
    )

    active_project, resolved_path, is_memory_url = await resolve_project_and_path(
        client=cast(Any, None),
        identifier="memory://research/notes/foo",
        context=ctx(context),
    )

    assert active_project.name == "Research"
    assert resolved_path == "team-paul/research/notes/foo"
    assert is_memory_url is True


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


@pytest.mark.asyncio
async def test_detect_project_from_memory_url_prefix_ignores_plain_paths(monkeypatch):
    import basic_memory.mcp.project_context as project_context
    from basic_memory.config import BasicMemoryConfig
    from basic_memory.mcp.project_context import detect_project_from_memory_url_prefix

    async def fail_if_called(context=None):  # pragma: no cover
        raise AssertionError("Plain paths must not trigger workspace discovery")

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fail_if_called)

    resolved = await detect_project_from_memory_url_prefix(
        "team-paul/main/notes/foo",
        BasicMemoryConfig(projects={}),
    )

    assert resolved is None


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
    async def test_local_route_clears_stale_cached_workspace(self, config_manager, monkeypatch):
        """A previous cloud workspace must not decorate later local memory URLs."""
        from contextlib import asynccontextmanager

        from mcp.server.fastmcp.exceptions import ToolError

        import basic_memory.mcp.project_context as project_context
        from basic_memory.config import ProjectEntry
        from basic_memory.mcp.project_context import get_project_client, resolve_project_and_path
        from basic_memory.schemas.project_info import ProjectItem
        from basic_memory.workspace_context import current_workspace_permalink_context

        config = config_manager.load_config()
        config.permalinks_include_project = True
        config.projects["local-proj"] = ProjectEntry(
            path=str(config_manager.config_dir.parent / "local-proj")
        )
        config_manager.save_config(config)

        context = ContextState()
        stale_workspace = _workspace(
            tenant_id="team-tenant",
            workspace_type="organization",
            slug="team-paul",
            name="Team Paul",
            role="editor",
        )
        await context.set_state("active_workspace", stale_workspace.model_dump())

        seen: dict[str, object] = {}
        active = ProjectItem(
            id=1,
            external_id="local-project-id",
            name="local-proj",
            path="/local-proj",
            is_default=False,
        )

        async def fail_ensure_workspace_project_index(context=None):  # pragma: no cover
            raise AssertionError("Local routing must not discover cloud workspaces")

        @asynccontextmanager
        async def fake_get_client(project_name=None, workspace=None):
            seen["project_name"] = project_name
            seen["workspace"] = workspace
            seen["permalink_context"] = current_workspace_permalink_context()
            yield object()

        async def fake_get_active_project(client, project_name, context=None, headers=None):
            assert project_name == "local-proj"
            return active

        async def fake_call_post(*args, **kwargs):
            raise ToolError("project not found")

        monkeypatch.setattr(
            project_context,
            "_ensure_workspace_project_index",
            fail_ensure_workspace_project_index,
        )
        monkeypatch.setattr("basic_memory.mcp.async_client.get_client", fake_get_client)
        monkeypatch.setattr(project_context, "get_active_project", fake_get_active_project)
        monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", fake_call_post)

        async with get_project_client(
            project="local-proj",
            context=ctx(context),
        ) as (client, active_project):
            _, resolved_path, is_memory_url = await resolve_project_and_path(
                client=cast(Any, client),
                identifier="memory://notes/foo",
                project="local-proj",
                context=ctx(context),
            )

        assert active_project == active
        assert seen == {
            "project_name": "local-proj",
            "workspace": None,
            "permalink_context": None,
        }
        assert resolved_path == "local-proj/notes/foo"
        assert is_memory_url is True
        assert await context.get_state("active_workspace") is None

    @pytest.mark.asyncio
    async def test_cloud_project_uses_per_project_workspace_id(self, config_manager, monkeypatch):
        """Cloud project with workspace_id uses cached workspace permalink context."""
        from basic_memory.mcp.project_context import get_project_client
        from basic_memory.config import ProjectEntry, ProjectMode
        from basic_memory.workspace_context import (
            WorkspacePermalinkContext,
            current_workspace_permalink_context,
        )

        config = config_manager.load_config()
        config.projects["cloud-proj"] = ProjectEntry(
            path=str(config_manager.config_dir.parent / "cloud-proj"),
            mode=ProjectMode.CLOUD,
            workspace_id="per-project-tenant-id",
        )
        config.cloud_api_key = "bmc_test123"
        config_manager.save_config(config)

        from basic_memory.schemas.project_info import ProjectItem

        seen: dict[str, object] = {}
        workspace = _workspace(
            tenant_id="per-project-tenant-id",
            workspace_type="organization",
            slug="team-paul",
            name="Team Paul",
            role="editor",
        )
        context = ContextState()
        await context.set_state("active_workspace", workspace.model_dump())

        async def fail_resolve_workspace_parameter(workspace=None, context=None):
            raise AssertionError(
                "Configured workspace_id should not prompt for workspace selection"
            )

        async def fail_ensure_workspace_project_index(context=None):  # pragma: no cover
            raise AssertionError("Configured workspace_id should not require workspace discovery")

        monkeypatch.setattr(
            "basic_memory.mcp.project_context.resolve_workspace_parameter",
            fail_resolve_workspace_parameter,
        )
        monkeypatch.setattr(
            "basic_memory.mcp.project_context._ensure_workspace_project_index",
            fail_ensure_workspace_project_index,
        )

        @asynccontextmanager
        async def fake_get_client(project_name=None, workspace=None):
            seen["project_name"] = project_name
            seen["workspace"] = workspace
            seen["permalink_context"] = current_workspace_permalink_context()
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

        async with get_project_client(
            project="cloud-proj",
            context=ctx(context),
        ) as (_client, active_project):
            assert active_project.external_id == "cloud-project-id"

        assert seen["project_name"] == "cloud-proj"
        assert seen["workspace"] == "per-project-tenant-id"
        permalink_context = cast(WorkspacePermalinkContext | None, seen["permalink_context"])
        assert permalink_context is not None
        assert permalink_context.workspace_slug == "team-paul"
        assert permalink_context.workspace_type == "organization"

    @pytest.mark.asyncio
    async def test_cloud_project_workspace_id_uses_workspace_provider_for_permalink_context(
        self, config_manager, monkeypatch
    ):
        """Injected workspace metadata supplies slug/type without project index discovery."""
        from contextlib import asynccontextmanager

        import basic_memory.mcp.project_context as project_context
        from basic_memory.mcp.project_context import get_project_client
        from basic_memory.config import ProjectEntry, ProjectMode
        from basic_memory.schemas.project_info import ProjectItem
        from basic_memory.workspace_context import (
            WorkspacePermalinkContext,
            current_workspace_permalink_context,
        )

        config = config_manager.load_config()
        config.projects["cloud-proj"] = ProjectEntry(
            path=str(config_manager.config_dir.parent / "cloud-proj"),
            mode=ProjectMode.CLOUD,
            workspace_id="per-project-tenant-id",
        )
        config.cloud_api_key = "bmc_test123"
        config_manager.save_config(config)

        workspace = _workspace(
            tenant_id="per-project-tenant-id",
            workspace_type="organization",
            slug="team-paul",
            name="Team Paul",
            role="editor",
        )

        async def fake_workspace_provider():
            return [workspace]

        async def fail_ensure_workspace_project_index(context=None):  # pragma: no cover
            raise AssertionError("Configured workspace_id should not require project discovery")

        monkeypatch.setattr(project_context, "_workspace_provider", fake_workspace_provider)
        monkeypatch.setattr(
            project_context,
            "_ensure_workspace_project_index",
            fail_ensure_workspace_project_index,
        )

        seen: dict[str, object] = {}

        @asynccontextmanager
        async def fake_get_client(project_name=None, workspace=None):
            seen["project_name"] = project_name
            seen["workspace"] = workspace
            seen["permalink_context"] = current_workspace_permalink_context()
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
        monkeypatch.setattr(project_context, "get_active_project", fake_get_active_project)

        async with get_project_client(project="cloud-proj") as (_client, active_project):
            assert active_project.external_id == "cloud-project-id"

        assert seen["project_name"] == "cloud-proj"
        assert seen["workspace"] == "per-project-tenant-id"
        permalink_context = cast(WorkspacePermalinkContext | None, seen["permalink_context"])
        assert permalink_context is not None
        assert permalink_context.workspace_slug == "team-paul"
        assert permalink_context.workspace_type == "organization"

    @pytest.mark.asyncio
    async def test_cloud_project_workspace_id_uses_cached_available_workspaces_for_permalink_context(
        self, config_manager, monkeypatch
    ):
        """Cached workspace metadata supplies slug/type before the provider is consulted."""
        from contextlib import asynccontextmanager

        import basic_memory.mcp.project_context as project_context
        from basic_memory.mcp.project_context import get_project_client
        from basic_memory.config import ProjectEntry, ProjectMode
        from basic_memory.schemas.project_info import ProjectItem
        from basic_memory.workspace_context import (
            WorkspacePermalinkContext,
            current_workspace_permalink_context,
        )

        config = config_manager.load_config()
        config.projects["cloud-proj"] = ProjectEntry(
            path=str(config_manager.config_dir.parent / "cloud-proj"),
            mode=ProjectMode.CLOUD,
            workspace_id="per-project-tenant-id",
        )
        config.cloud_api_key = "bmc_test123"
        config_manager.save_config(config)

        workspace = _workspace(
            tenant_id="per-project-tenant-id",
            workspace_type="organization",
            slug="team-paul",
            name="Team Paul",
            role="editor",
        )
        context = ContextState()
        await context.set_state("available_workspaces", ["ignored", workspace.model_dump()])

        async def fail_workspace_provider():  # pragma: no cover
            raise AssertionError("Cached workspace metadata should be used before provider")

        async def fail_ensure_workspace_project_index(context=None):  # pragma: no cover
            raise AssertionError("Configured workspace_id should not require project discovery")

        monkeypatch.setattr(project_context, "_workspace_provider", fail_workspace_provider)
        monkeypatch.setattr(
            project_context,
            "_ensure_workspace_project_index",
            fail_ensure_workspace_project_index,
        )

        seen: dict[str, object] = {}

        @asynccontextmanager
        async def fake_get_client(project_name=None, workspace=None):
            seen["project_name"] = project_name
            seen["workspace"] = workspace
            seen["permalink_context"] = current_workspace_permalink_context()
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
        monkeypatch.setattr(project_context, "get_active_project", fake_get_active_project)

        async with get_project_client(
            project="cloud-proj",
            context=ctx(context),
        ) as (_client, active_project):
            assert active_project.external_id == "cloud-project-id"

        assert seen["project_name"] == "cloud-proj"
        assert seen["workspace"] == "per-project-tenant-id"
        permalink_context = cast(WorkspacePermalinkContext | None, seen["permalink_context"])
        assert permalink_context is not None
        assert permalink_context.workspace_slug == "team-paul"
        assert permalink_context.workspace_type == "organization"

    @pytest.mark.asyncio
    async def test_cloud_project_workspace_id_errors_when_provider_omits_workspace(
        self, config_manager, monkeypatch
    ):
        """Provider metadata must include the configured tenant before slug/type forwarding."""
        import basic_memory.mcp.project_context as project_context
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

        async def fake_workspace_provider():
            return [
                _workspace(
                    tenant_id="other-tenant-id",
                    workspace_type="organization",
                    slug="other-team",
                    name="Other Team",
                    role="editor",
                )
            ]

        async def fail_ensure_workspace_project_index(context=None):  # pragma: no cover
            raise AssertionError("Configured workspace_id should not require project discovery")

        monkeypatch.setattr(project_context, "_workspace_provider", fake_workspace_provider)
        monkeypatch.setattr(
            project_context,
            "_ensure_workspace_project_index",
            fail_ensure_workspace_project_index,
        )

        with pytest.raises(
            ValueError,
            match="Configured workspace_id 'per-project-tenant-id' was not returned",
        ):
            async with get_project_client(project="cloud-proj"):
                pass

    @pytest.mark.asyncio
    async def test_cloud_project_workspace_id_routes_without_discovery(
        self, config_manager, monkeypatch
    ):
        """Configured workspace_id should route even when workspace metadata is unavailable."""
        from contextlib import asynccontextmanager

        import basic_memory.mcp.project_context as project_context
        from basic_memory.mcp.project_context import get_project_client
        from basic_memory.config import ProjectEntry, ProjectMode
        from basic_memory.schemas.project_info import ProjectItem
        from basic_memory.workspace_context import current_workspace_permalink_context

        config = config_manager.load_config()
        config.projects["cloud-proj"] = ProjectEntry(
            path=str(config_manager.config_dir.parent / "cloud-proj"),
            mode=ProjectMode.CLOUD,
            workspace_id="per-project-tenant-id",
        )
        config.cloud_api_key = "bmc_test123"
        config_manager.save_config(config)

        async def fail_ensure_workspace_project_index(context=None):  # pragma: no cover
            raise AssertionError("Configured workspace_id should not require workspace discovery")

        monkeypatch.setattr(
            project_context,
            "_ensure_workspace_project_index",
            fail_ensure_workspace_project_index,
        )

        seen: dict[str, object] = {}

        @asynccontextmanager
        async def fake_get_client(project_name=None, workspace=None):
            seen["project_name"] = project_name
            seen["workspace"] = workspace
            seen["permalink_context"] = current_workspace_permalink_context()
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
            project_context,
            "get_active_project",
            fake_get_active_project,
        )

        async with get_project_client(project="cloud-proj") as (_client, active_project):
            assert active_project.external_id == "cloud-project-id"

        assert seen["project_name"] == "cloud-proj"
        assert seen["workspace"] == "per-project-tenant-id"
        assert seen["permalink_context"] is None

    @pytest.mark.asyncio
    async def test_cloud_project_workspace_id_clears_stale_cached_workspace(
        self, config_manager, monkeypatch
    ):
        """Configured workspace_id must not reuse slug/type from another tenant."""
        from contextlib import asynccontextmanager

        import basic_memory.mcp.project_context as project_context
        from basic_memory.mcp.project_context import get_project_client
        from basic_memory.config import ProjectEntry, ProjectMode
        from basic_memory.schemas.project_info import ProjectItem
        from basic_memory.workspace_context import current_workspace_permalink_context

        config = config_manager.load_config()
        config.projects["cloud-proj"] = ProjectEntry(
            path=str(config_manager.config_dir.parent / "cloud-proj"),
            mode=ProjectMode.CLOUD,
            workspace_id="per-project-tenant-id",
        )
        config.cloud_api_key = "bmc_test123"
        config_manager.save_config(config)

        context = ContextState()
        stale_workspace = _workspace(
            tenant_id="other-tenant-id",
            workspace_type="organization",
            slug="other-team",
            name="Other Team",
            role="editor",
        )
        await context.set_state("active_workspace", stale_workspace.model_dump())

        async def fail_ensure_workspace_project_index(context=None):  # pragma: no cover
            raise AssertionError("Configured workspace_id should not require workspace discovery")

        monkeypatch.setattr(
            project_context,
            "_ensure_workspace_project_index",
            fail_ensure_workspace_project_index,
        )

        seen: dict[str, object] = {}

        @asynccontextmanager
        async def fake_get_client(project_name=None, workspace=None):
            seen["workspace"] = workspace
            seen["permalink_context"] = current_workspace_permalink_context()
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
        monkeypatch.setattr(project_context, "get_active_project", fake_get_active_project)

        async with get_project_client(
            project="cloud-proj",
            context=ctx(context),
        ) as (_client, active_project):
            assert active_project.external_id == "cloud-project-id"

        assert seen["workspace"] == "per-project-tenant-id"
        assert seen["permalink_context"] is None
        assert await context.get_state("active_workspace") is None

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
