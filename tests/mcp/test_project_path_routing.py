"""Direct unit tests for resolve_project_path_route (#1415, #1421).

The resolver is the single routing seam for the posix tools: '<project>/path'
inputs route by first segment, an explicit project must agree with a path
prefix, and unqualified input refuses instead of defaulting whenever more than
one project is addressable. The local-config tests drive the function
branch-by-branch against configs written through the test ConfigManager; no API
client is involved there because local-config matching never leaves the
process. The cloud section at the bottom stands up a factory-mode session,
where the project list comes from the tenant instead.
"""

import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import pytest

import basic_memory.mcp.async_client as async_client
import basic_memory.mcp.project_context as project_context
from basic_memory.config_models import ProjectEntry
from basic_memory.mcp.project_context import (
    ProjectPathRoute,
    ProjectPrefixConflictError,
    UnqualifiedPathRefusedError,
    _detected_route_remainder,
    _project_routes_agree,
    resolve_project_path_route,
)
from basic_memory.mcp.project_context_identifiers import unqualified_project_identifier
from basic_memory.mcp.tools import grep, ls
from basic_memory.schemas.cloud import WorkspaceInfo
from basic_memory.schemas.project_info import ProjectItem, ProjectList
from basic_memory.utils import generate_permalink
from tests.mcp.conftest import ContextState, ctx


@pytest.fixture(autouse=True)
def clean_project_env(monkeypatch):
    """The env constraint acts as an explicit project; clear it by default."""
    monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)


@pytest.fixture
def multi_project_config(config_manager, tmp_path_factory):
    """Three local projects, one with a spaced display name for permalink tests."""
    config = config_manager.load_config()
    config.projects["second-project"] = ProjectEntry(
        path=str(tmp_path_factory.mktemp("second-project-config"))
    )
    config.projects["My Research"] = ProjectEntry(
        path=str(tmp_path_factory.mktemp("my-research-config"))
    )
    config_manager.save_config(config)
    return config_manager


@pytest.fixture
def empty_project_config(config_manager):
    """A config with no projects at all (cloud-only local client)."""
    config = config_manager.load_config()
    config.projects = {}
    config_manager.save_config(config)
    return config_manager


# --- project_id passthrough ---


@pytest.mark.asyncio
async def test_project_id_bypasses_prefix_parsing(multi_project_config):
    """project_id routes by external UUID, so even a conflicting-looking prefix
    is never examined — documented limitation, mirroring read_note."""
    route = await resolve_project_path_route(
        "second-project/notes/foo",
        project="test-project",
        project_id="11111111-1111-1111-1111-111111111111",
    )

    assert route == ProjectPathRoute(
        project="test-project", path="second-project/notes/foo", stripped=False
    )


# --- rule 1: first-segment routing ---


@pytest.mark.asyncio
async def test_first_segment_routes_with_remainder(multi_project_config):
    route = await resolve_project_path_route(
        "second-project/notes/foo", project=None, project_id=None
    )

    assert route == ProjectPathRoute(project="second-project", path="notes/foo", stripped=True)


@pytest.mark.asyncio
async def test_single_segment_project_names_project_root(multi_project_config):
    """A bare project name routes with an empty remainder — ls 'second-project'
    lists that project's root."""
    route = await resolve_project_path_route("second-project", project=None, project_id=None)

    assert route == ProjectPathRoute(project="second-project", path="", stripped=True)


@pytest.mark.asyncio
async def test_memory_url_prefix_routes(multi_project_config):
    route = await resolve_project_path_route(
        "memory://second-project/notes/foo", project=None, project_id=None
    )

    assert route == ProjectPathRoute(project="second-project", path="notes/foo", stripped=True)


@pytest.mark.asyncio
async def test_namespace_syntax_routes(multi_project_config):
    """'project::note' normalizes to path syntax before prefix detection."""
    route = await resolve_project_path_route(
        "second-project::notes/foo", project=None, project_id=None
    )

    assert route == ProjectPathRoute(project="second-project", path="notes/foo", stripped=True)


@pytest.mark.asyncio
async def test_display_name_matches_by_permalink(multi_project_config):
    """The permalink form routes to the canonical config name, spaces and all."""
    route = await resolve_project_path_route("my-research/notes/foo", project=None, project_id=None)

    assert route == ProjectPathRoute(project="My Research", path="notes/foo", stripped=True)


@pytest.mark.asyncio
async def test_glob_first_segment_never_routes(multi_project_config):
    """split_project_prefix's '*' guard: a glob first segment is search input,
    not a mount — it falls through to the multi-project refusal."""
    with pytest.raises(UnqualifiedPathRefusedError, match=re.escape("no project 'second-*'")):
        await resolve_project_path_route("second-*/notes/foo", project=None, project_id=None)


# --- rule 2: explicit project agree/strip/conflict ---


@pytest.mark.asyncio
async def test_explicit_project_canonicalized_when_no_prefix(multi_project_config):
    """No recognized prefix: the path passes through untouched and the explicit
    project is canonicalized to its config spelling."""
    route = await resolve_project_path_route("notes/foo", project="my-research", project_id=None)

    assert route == ProjectPathRoute(project="My Research", path="notes/foo", stripped=False)


@pytest.mark.asyncio
async def test_agreeing_prefix_strips_with_explicit_project(multi_project_config):
    route = await resolve_project_path_route(
        "my-research/notes/foo", project="My Research", project_id=None
    )

    assert route == ProjectPathRoute(project="My Research", path="notes/foo", stripped=True)


@pytest.mark.asyncio
async def test_conflicting_prefix_raises_naming_both(multi_project_config):
    """The conflict message names both projects and offers both spellings."""
    with pytest.raises(ProjectPrefixConflictError) as excinfo:
        await resolve_project_path_route(
            "second-project/notes/foo", project="My Research", project_id=None
        )

    assert str(excinfo.value) == (
        "path names project 'second-project' but project 'My Research' was passed — "
        "use 'second-project/<path>' alone, or project='My Research' with a "
        "project-relative path"
    )


# --- workspace-qualified spellings ---
# The remainder/agreement helpers are pure functions; driving the qualified
# spellings through them directly avoids standing up cloud workspace discovery.


def test_detected_route_remainder_spelled_workspace_route_consumes_two_segments():
    """A workspace-qualified route spelled as both path segments consumes both."""
    assert _detected_route_remainder("other/research/notes/foo", "other/research") == "notes/foo"


def test_detected_route_remainder_bare_prefix_resolved_into_workspace_consumes_one():
    """A bare project prefix that resolved into a workspace consumed one segment."""
    assert _detected_route_remainder("research/notes/foo", "other/research") == "notes/foo"


def test_project_routes_agree_across_mixed_qualification():
    """A workspace-qualified spelling agrees with the unqualified spelling of
    the same project, in either direction; different projects never agree."""
    assert _project_routes_agree("research", "other/research")
    assert _project_routes_agree("other/research", "research")
    assert not _project_routes_agree("second-project", "other/research")


@pytest.mark.asyncio
async def test_explicit_workspace_qualified_project_survives_local_prefix_agreement(
    multi_project_config,
):
    """An explicit '<workspace>/<project>' param carries the route even when the
    path prefix matches a same-named local project — the explicitly named
    workspace is never silently swapped for the local shadow."""
    route = await resolve_project_path_route(
        "second-project/notes/foo", project="other/second-project", project_id=None
    )

    assert route == ProjectPathRoute(
        project="other/second-project", path="notes/foo", stripped=True
    )


# --- rule 4: multi-project refusal messages ---


@pytest.mark.asyncio
async def test_unqualified_path_refusal_lists_projects_sorted(multi_project_config):
    with pytest.raises(UnqualifiedPathRefusedError) as excinfo:
        await resolve_project_path_route("notes/foo", project=None, project_id=None)

    assert str(excinfo.value) == (
        "no project 'notes' — active projects: my-research/, second-project/, test-project/"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["", "/"])
async def test_empty_input_refuses_with_no_project_specified(multi_project_config, path):
    """grep/tail (no path) and a bare root land here in multi-project configs."""
    with pytest.raises(UnqualifiedPathRefusedError) as excinfo:
        await resolve_project_path_route(path, project=None, project_id=None)

    assert str(excinfo.value) == (
        "no project specified — active projects: my-research/, second-project/, test-project/"
    )


# --- single-project and empty-config passthrough ---


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["notes/foo", "test/root", "", "some-title"])
async def test_single_project_input_passes_through_unchanged(config_manager, path):
    """One configured project keeps today's default resolution; 'test' is not
    falsely stripped as a prefix of 'test-project' (permalink comparison)."""
    route = await resolve_project_path_route(path, project=None, project_id=None)

    assert route == ProjectPathRoute(project=None, path=path, stripped=False)


@pytest.mark.asyncio
async def test_empty_config_passes_through(empty_project_config):
    """An emptied config still materializes the placeholder 'main' project, so a
    locally routed session addresses exactly one project and keeps today's
    default resolution — there is nothing to disambiguate."""
    assert list(empty_project_config.config.projects) == ["main"]

    route = await resolve_project_path_route("anything/x", project=None, project_id=None)

    assert route == ProjectPathRoute(project=None, path="anything/x", stripped=False)


# --- env constraint as effective explicit project ---


@pytest.mark.asyncio
async def test_env_constraint_agreeing_prefix_strips(multi_project_config, monkeypatch):
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "second-project")

    route = await resolve_project_path_route(
        "second-project/notes/foo", project=None, project_id=None
    )

    assert route == ProjectPathRoute(project="second-project", path="notes/foo", stripped=True)


@pytest.mark.asyncio
async def test_env_constraint_conflicting_prefix_refuses(multi_project_config, monkeypatch):
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "test-project")

    with pytest.raises(ProjectPrefixConflictError, match="but project 'test-project' was passed"):
        await resolve_project_path_route("second-project/notes/foo", project=None, project_id=None)


@pytest.mark.asyncio
async def test_env_constraint_prevents_refusal_for_empty_input(multi_project_config, monkeypatch):
    """The constraint is an explicit project: grep/tail-style empty input routes
    to it instead of refusing."""
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "second-project")

    route = await resolve_project_path_route("", project=None, project_id=None)

    assert route == ProjectPathRoute(project="second-project", path="", stripped=False)


# --- cloud/factory sessions (#1421) ---
# The hosted MCP server installs a client factory and keeps project state in the
# tenant database, not in config: BasicMemoryConfig always materializes a
# placeholder 'main' entry, so local config proves nothing about a cloud
# session's projects. These tests stand up that shape and pin the invariant
# that ties the mount view to the resolver — everything `ls "/"` advertises must
# be addressable — plus the refusal that keeps an unqualified reference from
# landing on whichever project a teammate last flagged as default.


@dataclass
class _CloudSession:
    """What a stood-up cloud session exposes to a test."""

    projects: tuple[ProjectItem, ...]
    listings: list[ProjectList]


def _routed_project_permalink(route: ProjectPathRoute) -> str:
    """The project a route landed on, as its bare permalink.

    Cloud routes come back workspace-qualified ('team/research'), so compare on
    the project segment the mount view advertises.
    """
    assert route.project is not None
    return generate_permalink(unqualified_project_identifier(route.project))


@pytest.fixture
def cloud_session(monkeypatch, config_manager):
    """Build a factory-mode session whose workspace holds the named projects.

    Both the mount view and workspace discovery read the same project listing
    the tenant serves, exactly as they do in production: the injected factory
    client answers `get_client()` for the mount view and `get_client(workspace=)`
    for the workspace index. Every listing call is recorded so tests can pin how
    often routing pays for it.
    """

    def build(*names: str, default: Optional[str] = None) -> _CloudSession:
        config = config_manager.load_config()
        # The cloud nulls its config cache so default_project reads as None;
        # projects={} still round-trips back as the placeholder 'main' entry.
        config.projects = {}
        config.default_project = None
        config_manager.save_config(config)

        projects = tuple(
            ProjectItem(
                id=index + 1,
                external_id=f"{generate_permalink(name)}-external-id",
                name=name,
                path=f"/app/data/{generate_permalink(name)}",
                is_default=name == default,
            )
            for index, name in enumerate(names)
        )
        project_list = ProjectList(projects=list(projects), default_project=default)
        workspace = WorkspaceInfo(
            tenant_id="team-tenant",
            workspace_type="organization",
            slug="team",
            name="Team",
            role="editor",
            is_default=True,
        )

        listings: list[ProjectList] = []

        @asynccontextmanager
        async def fake_get_client(*args, **kwargs) -> AsyncIterator[object]:
            yield object()

        async def fake_list_projects(self) -> ProjectList:
            listings.append(project_list)
            return project_list

        async def fake_get_available_workspaces(context=None) -> list[WorkspaceInfo]:
            return [workspace]

        monkeypatch.setattr(async_client, "is_factory_mode", lambda: True)
        monkeypatch.setattr(async_client, "get_client", fake_get_client)
        monkeypatch.setattr(
            "basic_memory.mcp.clients.project.ProjectClient.list_projects",
            fake_list_projects,
        )
        monkeypatch.setattr(
            project_context,
            "get_available_workspaces",
            fake_get_available_workspaces,
        )
        return _CloudSession(projects=projects, listings=listings)

    return build


@pytest.mark.asyncio
async def test_cloud_workspace_refuses_unqualified_path(cloud_session):
    """The reported failure (#1421): with only a placeholder config entry to
    enumerate, an unqualified path used to route to a default project. It now
    refuses, naming every addressable project in copyable prefix form."""
    cloud_session("research", "engineering", "personal-notes")

    with pytest.raises(UnqualifiedPathRefusedError) as excinfo:
        await resolve_project_path_route("notes/foo", project=None, project_id=None)

    assert str(excinfo.value) == (
        "no project 'notes' — active projects: engineering/, personal-notes/, research/"
    )


@pytest.mark.asyncio
async def test_cloud_workspace_refuses_pathless_call_through_the_tool(cloud_session):
    """grep/tail carry no path to qualify, so the refusal is the tool's answer:
    a silent search of one project out of several is the bug being removed."""
    cloud_session("research", "engineering")

    with pytest.raises(UnqualifiedPathRefusedError) as excinfo:
        await grep("needle")

    assert str(excinfo.value) == ("no project specified — active projects: engineering/, research/")


@pytest.mark.asyncio
async def test_cloud_qualified_path_routes_to_that_project(cloud_session):
    """A qualified '<project>/notes/x' routes to that exact project — the
    workspace-qualified spelling keeps the tenant explicit — and the remainder
    becomes the project-relative path."""
    cloud_session("research", "engineering", "personal-notes")

    route = await resolve_project_path_route("research/notes/x", project=None, project_id=None)

    assert route.stripped is True
    assert route.path == "notes/x"
    assert route.project == "team/research"
    assert unqualified_project_identifier(route.project) == "research"


@pytest.mark.asyncio
async def test_cloud_every_advertised_mount_is_addressable(cloud_session):
    """Round trip over the advertised list: `ls "/"` and the resolver read one
    source, so every mount the root advertises routes — as a bare mount name
    and as a path under it. A mount that listed but did not route is exactly
    the mismatch #1421 reported."""
    cloud_session("research", "engineering", "personal-notes")

    mounts = await ls()

    assert [node["name"] for node in mounts["nodes"]] == [
        "engineering",
        "personal-notes",
        "research",
    ]
    for node in mounts["nodes"]:
        permalink = node["permalink"]
        assert node["directory_path"] == f"/{permalink}"

        root_route = await resolve_project_path_route(permalink, project=None, project_id=None)
        assert root_route.stripped is True
        assert root_route.path == ""
        assert _routed_project_permalink(root_route) == permalink

        path_route = await resolve_project_path_route(
            f"{permalink}/notes/x", project=None, project_id=None
        )
        assert path_route.stripped is True
        assert path_route.path == "notes/x"
        assert _routed_project_permalink(path_route) == permalink


@pytest.mark.asyncio
async def test_cloud_single_project_workspace_resolves_unqualified(cloud_session):
    """One project in the workspace means no ambiguity to protect against, so
    unqualified references keep resolving unchanged."""
    cloud_session("research")

    route = await resolve_project_path_route("notes/foo", project=None, project_id=None)

    assert route == ProjectPathRoute(project=None, path="notes/foo", stripped=False)


@pytest.mark.asyncio
async def test_cloud_refusal_does_not_consult_the_default_flag(cloud_session):
    """is_default is one shared mutable flag in a team workspace, so it must not
    decide where an unqualified call lands: a workspace whose default points at
    'alpha' still refuses while 'beta' and 'gamma' exist."""
    session = cloud_session("alpha", "beta", "gamma", default="alpha")

    assert [project.name for project in session.projects if project.is_default] == ["alpha"]

    with pytest.raises(UnqualifiedPathRefusedError) as excinfo:
        await resolve_project_path_route("", project=None, project_id=None)

    # The flagged project is listed as one choice among equals, never selected.
    assert str(excinfo.value) == "no project specified — active projects: alpha/, beta/, gamma/"

    # And it hijacks nothing that named a project: qualified input still routes
    # where the caller said, not to whatever carries the flag.
    route = await resolve_project_path_route("beta/notes/x", project=None, project_id=None)

    assert _routed_project_permalink(route) == "beta"


@pytest.mark.asyncio
async def test_cloud_project_listing_is_fetched_once_per_request(cloud_session):
    """The cost of knowing the workspace's projects: one listing per MCP
    request, memoized in context state, no matter how many resolutions the
    request makes. Without a context (CLI calls) each resolution pays."""
    session = cloud_session("research", "engineering")
    context = ContextState()

    first = await resolve_project_path_route(
        "research", project=None, project_id=None, context=ctx(context)
    )
    assert first.stripped is True
    assert len(session.listings) == 1

    session.listings.clear()
    second = await resolve_project_path_route(
        "engineering", project=None, project_id=None, context=ctx(context)
    )

    assert second.stripped is True
    assert session.listings == []
