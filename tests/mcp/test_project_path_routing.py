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
    _project_routes_agree,
    resolve_project_path_route,
    resolve_workspace_project_identifier,
)
from basic_memory.mcp.project_context_identifiers import (
    split_workspace_route_segments,
    unqualified_project_identifier,
)
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
    is never examined — documented limitation, mirroring read_note. The route
    echoes the caller's id so tool call sites can pass the route alone."""
    route = await resolve_project_path_route(
        "second-project/notes/foo",
        project="test-project",
        project_id="11111111-1111-1111-1111-111111111111",
    )

    assert route == ProjectPathRoute(
        project="test-project",
        path="second-project/notes/foo",
        stripped=False,
        project_id="11111111-1111-1111-1111-111111111111",
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
async def test_multi_segment_project_permalink_routes(config_manager, tmp_path_factory):
    """Project names may contain '/', and generate_permalink keeps it, so a
    mount's permalink can span several segments. Comparing only the first
    segment advertised '/research/2026' at the root and then could not enter it;
    the longest matching permalink wins, so the nested name beats its own
    prefix."""
    config = config_manager.load_config()
    config.projects["Research"] = ProjectEntry(path=str(tmp_path_factory.mktemp("research")))
    config.projects["Research/2026"] = ProjectEntry(
        path=str(tmp_path_factory.mktemp("research-2026"))
    )
    config_manager.save_config(config)

    root = await resolve_project_path_route("research/2026", project=None, project_id=None)
    assert root == ProjectPathRoute(project="Research/2026", path="", stripped=True)

    nested = await resolve_project_path_route(
        "research/2026/notes/x", project=None, project_id=None
    )
    assert nested == ProjectPathRoute(project="Research/2026", path="notes/x", stripped=True)

    # The one-segment mount still claims paths its longer sibling does not.
    shallow = await resolve_project_path_route("research/notes/x", project=None, project_id=None)
    assert shallow == ProjectPathRoute(project="Research", path="notes/x", stripped=True)


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
# The agreement helper is a pure function; driving the qualified spellings
# through it directly avoids standing up cloud workspace discovery. The
# remainder is no longer derived separately — it comes from the same parse that
# matched the route, so the qualified-path tests below pin it end to end.


def test_split_workspace_route_segments_needs_two_named_segments():
    """The path form makes the trailing path optional, but both route segments
    still have to be there: one segment names no project, and an empty one (a
    '//' in the input) names nothing at all."""
    assert split_workspace_route_segments("acme") is None
    assert split_workspace_route_segments("acme//docs") is None
    assert split_workspace_route_segments("acme/docs") == ("acme", "docs", "")
    assert split_workspace_route_segments("acme/docs/notes/x") == ("acme", "docs", "notes/x")


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
    """A qualified '<project>/notes/x' routes to that exact project by the mount
    name the root advertises, with the remainder as the project-relative path.

    The mount table is already scoped to this session's own route, so it is the
    authority on that first segment; `ls "research"` has always routed by the
    bare name, and the path form now agrees with it instead of qualifying the
    tenant only when a slash happened to be present.
    """
    cloud_session("research", "engineering", "personal-notes")

    route = await resolve_project_path_route("research/notes/x", project=None, project_id=None)

    assert route == ProjectPathRoute(
        project="research",
        path="notes/x",
        stripped=True,
        project_id="research-external-id",
    )
    assert unqualified_project_identifier(route.project or "") == "research"


@pytest.mark.asyncio
async def test_cloud_workspace_qualified_path_without_mount_collision_still_routes(cloud_session):
    """No project is named 'team', so the workspace slug is free to claim the
    first segment: '<workspace>/<project>/<path>' resolves through workspace
    discovery, qualified name and all. Mount precedence takes nothing away from
    the spellings that address other workspaces."""
    cloud_session("research", "engineering")

    route = await resolve_project_path_route("team/research/notes/x", project=None, project_id=None)

    assert route == ProjectPathRoute(project="team/research", path="notes/x", stripped=True)


@pytest.mark.asyncio
async def test_cloud_workspace_qualified_project_root_routes(cloud_session):
    """'<workspace>/<project>' with no path names that project's root, exactly as
    a bare mount name does. Only the three-segment form used to resolve, so a
    cross-workspace project could be listed into ('acme/docs/notes') but its own
    root ('acme/docs') had no spelling at all — it fell through to the
    multi-project refusal."""
    cloud_session("research", "engineering")

    route = await resolve_project_path_route("team/research", project=None, project_id=None)

    assert route == ProjectPathRoute(project="team/research", path="", stripped=True)


@pytest.mark.asyncio
async def test_cloud_workspace_project_root_falls_through_without_workspaces(
    cloud_session, monkeypatch
):
    """Workspace discovery stays best-effort for prefix detection: with no
    reachable workspace, '<workspace>/<project>' is simply not a route and lands
    on the ordinary refusal. Input that shaped like a workspace route may never
    have meant one, so a discovery failure must not become the caller's error."""
    cloud_session("research", "engineering")

    async def no_workspaces(context=None) -> list[WorkspaceInfo]:
        return []

    monkeypatch.setattr(project_context, "get_available_workspaces", no_workspaces)

    with pytest.raises(UnqualifiedPathRefusedError) as excinfo:
        await resolve_project_path_route("acme/docs", project=None, project_id=None)

    assert str(excinfo.value) == "no project 'acme' — active projects: engineering/, research/"


@pytest.mark.asyncio
async def test_cloud_mount_wins_over_colliding_workspace_slug(cloud_session):
    """The collision: 'team' is both an advertised mount and this workspace's
    slug, and that workspace also holds a project 'docs'. The mount wins the
    first segment, so 'team/docs/x' is project 'team', path 'docs/x' — never
    project 'docs' in workspace 'team'. Attempting workspace-qualified parsing
    first served another project's data under an advertised name."""
    cloud_session("team", "docs")

    route = await resolve_project_path_route("team/docs/x", project=None, project_id=None)

    assert route == ProjectPathRoute(
        project="team", path="docs/x", stripped=True, project_id="team-external-id"
    )


@pytest.mark.asyncio
async def test_cloud_slug_collision_shadowed_project_stays_addressable(cloud_session):
    """The documented cost of mount-wins, pinned: while a mount named 'team'
    exists, project 'docs' in workspace 'team' loses its qualified *path*
    spelling. It stays addressable through the project param — project-relative,
    or with an agreeing prefix — and the shadowed spelling conflicts loudly
    rather than resolving either way."""
    cloud_session("team", "docs")

    relative = await resolve_project_path_route("x", project="team/docs", project_id=None)
    assert relative == ProjectPathRoute(project="team/docs", path="x", stripped=False)

    agreeing = await resolve_project_path_route("docs/x", project="team/docs", project_id=None)
    assert agreeing == ProjectPathRoute(project="team/docs", path="x", stripped=True)

    with pytest.raises(ProjectPrefixConflictError, match="path names project 'team'"):
        await resolve_project_path_route("team/docs/x", project="team/docs", project_id=None)


@pytest.mark.asyncio
async def test_cloud_every_advertised_mount_is_addressable_under_slug_collision(cloud_session):
    """The round-trip invariant survives the collision, which is the point of
    mount precedence: a mount named after the workspace slug still routes to
    itself, and so does every other mount beside it."""
    cloud_session("team", "docs")

    mounts = await ls()

    assert [node["name"] for node in mounts["nodes"]] == ["docs", "team"]
    for node in mounts["nodes"]:
        permalink = node["permalink"]

        external_id = f"{permalink}-external-id"

        root_route = await resolve_project_path_route(permalink, project=None, project_id=None)
        assert root_route == ProjectPathRoute(
            project=node["name"], path="", stripped=True, project_id=external_id
        )

        path_route = await resolve_project_path_route(
            f"{permalink}/notes/x", project=None, project_id=None
        )
        assert path_route == ProjectPathRoute(
            project=node["name"], path="notes/x", stripped=True, project_id=external_id
        )


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


# --- mounts stay bound to the workspace that advertised them (#1421) ---
# A hosted session's own route is one tenant, but workspace discovery reaches
# every workspace the account can see, and project names are unique only inside
# one of them. These tests stand up that shape: two accessible workspaces, the
# session bound to the non-default one, and the same project permalink in both.

_SESSION_DOCS_ID = "11111111-1111-1111-1111-111111111111"
_DEFAULT_DOCS_ID = "22222222-2222-2222-2222-222222222222"
_SESSION_RESEARCH_ID = "33333333-3333-3333-3333-333333333333"
_SESSION_ENGINEERING_ID = "44444444-4444-4444-4444-444444444444"
_DEFAULT_NOTES_ID = "55555555-5555-5555-5555-555555555555"

# Every (workspace, project) pair gets its own id: the same project name living
# in two workspaces is the whole point of this section, and the id is what pins
# a mount to the workspace that advertised it. UUID-shaped because the index
# looks an external_id up as a UUID before falling back to a name search.
_WORKSPACE_PROJECT_IDS = {
    ("session-tenant", "docs"): _SESSION_DOCS_ID,
    ("session-tenant", "research"): _SESSION_RESEARCH_ID,
    ("session-tenant", "engineering"): _SESSION_ENGINEERING_ID,
    ("default-tenant", "docs"): _DEFAULT_DOCS_ID,
    ("default-tenant", "notes"): _DEFAULT_NOTES_ID,
}


@dataclass
class _FakeHttpClient:
    """Stands in for the routed client, carrying only the workspace selector."""

    workspace: Optional[str]


def _tenant_listing(tenant_id: str, names: tuple[str, ...]) -> ProjectList:
    """Build one tenant's project listing, the first name carrying is_default."""
    return ProjectList(
        projects=[
            ProjectItem(
                id=index + 1,
                external_id=_WORKSPACE_PROJECT_IDS[(tenant_id, name)],
                name=name,
                path=f"/app/data/{generate_permalink(name)}",
                is_default=index == 0,
            )
            for index, name in enumerate(names)
        ],
        default_project=names[0],
    )


@pytest.fixture
def cross_workspace_session(monkeypatch, config_manager):
    """Build a factory session on a non-default workspace beside the default one.

    ``get_client()`` with no selector is the session's own tenant — what the
    hosted server hands a call that names no workspace, and therefore what the
    mount view lists. ``get_client(workspace=...)`` is how the workspace index
    reaches each accessible tenant, so the two answer with different projects.
    """

    def build(
        *,
        failed_tenant: Optional[str] = None,
        session_projects: tuple[str, ...] = ("docs",),
        default_projects: tuple[str, ...] = ("docs",),
    ) -> tuple[WorkspaceInfo, WorkspaceInfo]:
        config = config_manager.load_config()
        config.projects = {}
        config.default_project = None
        config_manager.save_config(config)

        session_workspace = WorkspaceInfo(
            tenant_id="session-tenant",
            workspace_type="organization",
            slug="beta",
            name="Beta",
            role="editor",
            is_default=False,
        )
        default_workspace = WorkspaceInfo(
            tenant_id="default-tenant",
            workspace_type="personal",
            slug="acme",
            name="Acme",
            role="owner",
            is_default=True,
        )
        listings = {
            "session-tenant": _tenant_listing("session-tenant", session_projects),
            "default-tenant": _tenant_listing("default-tenant", default_projects),
        }

        @asynccontextmanager
        async def fake_get_client(*args, **kwargs) -> AsyncIterator[object]:
            yield _FakeHttpClient(workspace=kwargs.get("workspace"))

        async def fake_list_projects(self) -> ProjectList:
            tenant = self.http_client.workspace or session_workspace.tenant_id
            if tenant == failed_tenant:
                raise RuntimeError(f"tenant {tenant} is unavailable")
            return listings[tenant]

        async def fake_get_available_workspaces(context=None) -> list[WorkspaceInfo]:
            return [session_workspace, default_workspace]

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
        return session_workspace, default_workspace

    return build


@pytest.mark.asyncio
async def test_cloud_mount_routes_by_the_id_that_names_its_workspace(cross_workspace_session):
    """The mount view lists this session's own tenant, so a mount it advertises
    has to route there. Carrying only the bare name let the cross-workspace
    index re-resolve 'docs' by the is_default flag: on a first call — before any
    workspace is cached — `cat("docs/x")` read the default workspace's 'docs'
    under a name the session workspace advertised."""
    session_workspace, default_workspace = cross_workspace_session()

    route = await resolve_project_path_route("docs/x", project=None, project_id=None)

    assert route == ProjectPathRoute(
        project="docs", path="x", stripped=True, project_id=_SESSION_DOCS_ID
    )

    # The id is what the shared index consumes, and it is the half that decides:
    # by id the route lands in the session's workspace, while the bare name
    # still falls through to whichever workspace carries the default flag.
    by_id = await resolve_workspace_project_identifier(route.project_id or "")
    by_name = await resolve_workspace_project_identifier("docs")

    assert by_id.workspace.tenant_id == session_workspace.tenant_id
    assert by_name.workspace.tenant_id == default_workspace.tenant_id


@pytest.mark.asyncio
async def test_cloud_explicit_qualified_project_drops_the_mount_id(cross_workspace_session):
    """The escape hatch keeps working: an explicit '<workspace>/<project>' names
    a workspace of its own, so it must not inherit the mount's id and be routed
    back to this session's tenant."""
    cross_workspace_session()

    route = await resolve_project_path_route("docs/x", project="acme/docs", project_id=None)

    assert route == ProjectPathRoute(project="acme/docs", path="x", stripped=True, project_id=None)


@pytest.mark.asyncio
async def test_cloud_workspace_project_root_surfaces_a_failed_workspace(cross_workspace_session):
    """A '<workspace>/<project>' root whose workspace could not be listed is a
    real failure, not an unrecognized path: it must say so rather than fall
    through to a refusal that claims the project does not exist."""
    cross_workspace_session(failed_tenant="default-tenant")

    with pytest.raises(ValueError, match="could not be loaded"):
        await resolve_project_path_route("acme/docs", project=None, project_id=None)


# --- an unqualified first segment never leaves this session's workspace (#1421) ---
# The companion to mount-id binding above. That one covers a name present in
# both workspaces; these cover a name present only in the *other* one, where
# there is no mount to claim the segment and the workspace fallback used to
# resolve the bare name across every accessible workspace.


@pytest.mark.asyncio
async def test_cloud_unqualified_first_segment_never_reaches_another_workspace(
    cross_workspace_session,
):
    """The leak: this session's workspace has no 'notes', another accessible
    workspace does, and `cat("notes/foo")` is an ordinary project-relative path.
    Resolving the bare first segment against every accessible workspace found
    the other tenant's 'notes' and read it. It must refuse instead, naming only
    the mounts this session can actually address."""
    cross_workspace_session(
        session_projects=("research", "engineering"),
        default_projects=("notes",),
    )

    with pytest.raises(UnqualifiedPathRefusedError) as excinfo:
        await resolve_project_path_route("notes/foo", project=None, project_id=None)

    # The other workspace's project is named nowhere in the refusal — it was
    # never addressable from here, so advertising it would teach a wrong route.
    assert str(excinfo.value) == ("no project 'notes' — active projects: engineering/, research/")


@pytest.mark.asyncio
async def test_cloud_unqualified_first_segment_stays_local_in_a_single_project_workspace(
    cross_workspace_session,
):
    """Same shape, one project in this workspace: rule 5 has no ambiguity to
    refuse, so the path stays unstripped for the ordinary default resolution.
    The point is where it does *not* go — a lone mount must not make the
    cross-workspace name lookup the tiebreaker."""
    cross_workspace_session(session_projects=("research",), default_projects=("notes",))

    route = await resolve_project_path_route("notes/foo", project=None, project_id=None)

    assert route == ProjectPathRoute(project=None, path="notes/foo", stripped=False)


@pytest.mark.asyncio
async def test_cloud_empty_route_segment_is_not_a_workspace_route(cross_workspace_session):
    """An empty segment names nothing, so 'acme//notes' is not the qualified
    spelling of anything even though 'acme' is a real workspace slug. It refuses
    rather than being repaired into a route the caller did not write."""
    cross_workspace_session(
        session_projects=("research", "engineering"),
        default_projects=("notes",),
    )

    with pytest.raises(UnqualifiedPathRefusedError) as excinfo:
        await resolve_project_path_route("acme//notes", project=None, project_id=None)

    assert str(excinfo.value) == ("no project 'acme' — active projects: engineering/, research/")


@pytest.mark.asyncio
async def test_cloud_other_workspace_stays_reachable_when_qualified(cross_workspace_session):
    """Refuse-don't-default takes no address away: naming the workspace is how a
    caller reaches it deliberately, as a path and through the project param, and
    both spellings still land on the other tenant's project."""
    cross_workspace_session(
        session_projects=("research", "engineering"),
        default_projects=("notes",),
    )

    qualified_path = await resolve_project_path_route(
        "acme/notes/foo", project=None, project_id=None
    )
    assert qualified_path == ProjectPathRoute(project="acme/notes", path="foo", stripped=True)

    root = await resolve_project_path_route("acme/notes", project=None, project_id=None)
    assert root == ProjectPathRoute(project="acme/notes", path="", stripped=True)

    explicit = await resolve_project_path_route("foo", project="acme/notes", project_id=None)
    assert explicit == ProjectPathRoute(project="acme/notes", path="foo", stripped=False)
