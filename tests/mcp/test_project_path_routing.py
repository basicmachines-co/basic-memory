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
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import pytest

import basic_memory.mcp.async_client as async_client
import basic_memory.mcp.project_context as project_context
from basic_memory.config import ConfigManager
from basic_memory.config_models import ProjectEntry
from basic_memory.mcp.project_context import (
    AddressableProject,
    DetectedProjectRoute,
    ProjectPathRoute,
    ProjectPrefixConflictError,
    AmbiguousMountError,
    UnqualifiedPathRefusedError,
    _agreed_route_project,
    detect_project_from_identifier_prefix,
    invalidate_project_caches,
    resolve_project_path_route,
    resolve_workspace_project_identifier,
    resolve_workspace_qualified_identifier,
    resolve_workspace_qualified_memory_url,
)
from basic_memory.mcp.project_context_identifiers import (
    split_project_permalink_prefix,
)
from basic_memory.mcp.tools import grep, ls
from basic_memory.mcp.workspace_project_index import (
    WorkspaceProjectEntry,
    WorkspaceProjectIndex,
    build_workspace_project_index,
    resolve_workspace_project_from_index,
)
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
async def test_extension_bearing_project_name_names_project_root(config_manager, tmp_path_factory):
    """An exact display name remains a project-root escape hatch even when its
    extension would be stripped from the project's permalink."""
    config = config_manager.load_config()
    config.projects["docs.txt"] = ProjectEntry(
        path=str(tmp_path_factory.mktemp("extension-project"))
    )
    config_manager.save_config(config)

    route = await resolve_project_path_route("docs.txt", project=None, project_id=None)

    assert route == ProjectPathRoute(project="docs.txt", path="", stripped=True)


@pytest.mark.asyncio
async def test_extension_bearing_project_name_root_accepts_normalized_spelling(
    config_manager, tmp_path_factory
):
    config = config_manager.load_config()
    config.projects["My Docs.txt"] = ProjectEntry(path=str(tmp_path_factory.mktemp("my-docs-txt")))
    config.projects["Research"] = ProjectEntry(path=str(tmp_path_factory.mktemp("research")))
    config_manager.save_config(config)

    route = await resolve_project_path_route("my-docs.txt", project=None, project_id=None)

    assert route == ProjectPathRoute(project="My Docs.txt", path="", stripped=True)


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
async def test_multi_segment_mount_agrees_with_explicit_workspace_spelling(
    config_manager, tmp_path_factory
):
    """The explicit-workspace escape hatch has to survive slash-bearing names.

    With mount 'Research/2026' detected and project='acme/Research/2026' passed,
    inferring qualification from the first slash read the detected mount as
    workspace 'Research' plus project '2026', so two agreeing spellings were
    rejected as a conflict. Segment count settles it without guessing.
    """
    config = config_manager.load_config()
    config.projects["Research/2026"] = ProjectEntry(
        path=str(tmp_path_factory.mktemp("research-2026-agree"))
    )
    config_manager.save_config(config)

    route = await resolve_project_path_route(
        "research/2026/notes/x", project="acme/Research/2026", project_id=None
    )

    # The explicitly named workspace survives, and the mount id goes with it.
    assert route == ProjectPathRoute(
        project="acme/Research/2026", path="notes/x", stripped=True, project_id=None
    )


@pytest.mark.asyncio
async def test_colliding_mount_permalinks_refuse_rather_than_pick(config_manager, tmp_path_factory):
    """Two names that normalize to one permalink are one address for two
    projects, and the resolver must not choose.

    add_project refuses this now, so only a config written before that check or
    edited by hand can reach it — but silently keeping whichever sorted last
    read that project's content under the other's name, which is worse than a
    loud failure naming both.
    """
    config = config_manager.load_config()
    config.projects["My Docs"] = ProjectEntry(path=str(tmp_path_factory.mktemp("my-docs-a")))
    config.projects["my-docs"] = ProjectEntry(path=str(tmp_path_factory.mktemp("my-docs-b")))
    config_manager.save_config(config)

    with pytest.raises(AmbiguousMountError) as excinfo:
        await resolve_project_path_route("my-docs/notes", project=None, project_id=None)

    assert "My Docs" in str(excinfo.value)
    assert "my-docs" in str(excinfo.value)


@pytest.mark.asyncio
async def test_colliding_mounts_only_fail_the_paths_that_name_them(
    config_manager, tmp_path_factory
):
    """An ambiguity fails the calls that depend on it, and no others.

    Raising while building the lookup table rejected every non-empty path in the
    session, so an unrelated project became unreachable and the exact-name
    escape hatch the error itself recommends did not work — that call never goes
    through the ambiguous lookup at all.
    """
    config = config_manager.load_config()
    config.projects["My Docs"] = ProjectEntry(path=str(tmp_path_factory.mktemp("dup-a")))
    config.projects["my-docs"] = ProjectEntry(path=str(tmp_path_factory.mktemp("dup-b")))
    config.projects["Other"] = ProjectEntry(path=str(tmp_path_factory.mktemp("dup-other")))
    config_manager.save_config(config)

    # A path claimed by an unrelated mount is unaffected.
    unrelated = await resolve_project_path_route("other/note", project=None, project_id=None)
    assert unrelated == ProjectPathRoute(project="Other", path="note", stripped=True)

    # So is the escape hatch, for the unrelated project...
    hatch = await resolve_project_path_route("note", project="Other", project_id=None)
    assert hatch == ProjectPathRoute(project="Other", path="note", stripped=False)

    # ...and for either side of the collision, which naming exactly must reach.
    for name in ("My Docs", "my-docs"):
        route = await resolve_project_path_route("note", project=name, project_id=None)
        assert route == ProjectPathRoute(project=name, path="note", stripped=False)

    # The path that genuinely names both still refuses, naming both.
    with pytest.raises(AmbiguousMountError) as excinfo:
        await resolve_project_path_route("my-docs/note", project=None, project_id=None)
    assert "My Docs" in str(excinfo.value)
    assert "my-docs" in str(excinfo.value)


@pytest.mark.asyncio
async def test_sibling_slash_bearing_project_conflicts_with_its_prefix(
    config_manager, tmp_path_factory
):
    """'docs' and 'team/docs' are two projects, not two spellings of one.

    The path names the 'team/docs' mount while the caller named 'docs', so this
    is the ordinary prefix conflict — but the shape heuristic read the extra
    leading segment as a workspace qualifier, made them agree, and discarded the
    explicit selection to read the other project instead.
    """
    config = config_manager.load_config()
    config.projects["docs"] = ProjectEntry(path=str(tmp_path_factory.mktemp("docs-sibling")))
    config.projects["team/docs"] = ProjectEntry(
        path=str(tmp_path_factory.mktemp("team-docs-sibling"))
    )
    config_manager.save_config(config)

    with pytest.raises(ProjectPrefixConflictError) as excinfo:
        await resolve_project_path_route("team/docs/note", project="docs", project_id=None)

    assert "team/docs" in str(excinfo.value)
    assert "'docs' was passed" in str(excinfo.value)

    # Each project is still reachable by naming it and giving a relative path.
    for name in ("docs", "team/docs"):
        route = await resolve_project_path_route(f"{name}/note", project=name, project_id=None)
        assert route == ProjectPathRoute(project=name, path="note", stripped=True)


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
async def test_same_named_note_is_not_read_as_its_own_project(multi_project_config):
    """A note whose file stem equals its project's name resolves as a
    project-relative path, not as a prefix claim with an empty remainder
    (#1458). Without the fix, rule 3b's extension-stripping match claims
    'second-project.txt' as the project itself, leaving 'cat' unable to tell
    it apart from a bare project-root reference."""
    route = await resolve_project_path_route(
        "second-project.txt", project="second-project", project_id=None
    )

    assert route == ProjectPathRoute(
        project="second-project", path="second-project.txt", stripped=False
    )


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


def test_split_project_permalink_prefix_matches_whole_permalinks_longest_first():
    """The one place a path becomes (project, project-relative path). It takes
    the candidate set precisely because how many segments a project consumes is
    a fact about the known projects, not about the string."""
    permalinks = ["research", "research/2026"]

    assert split_project_permalink_prefix("research/2026/notes", permalinks) == (
        "research/2026",
        "notes",
    )
    assert split_project_permalink_prefix("research/notes", permalinks) == ("research", "notes")
    assert split_project_permalink_prefix("research/2026", permalinks) == ("research/2026", "")
    assert split_project_permalink_prefix("engineering/notes", permalinks) is None
    # Spelling is normalized per segment, so display names match their permalink.
    assert split_project_permalink_prefix("My Research/notes", ["my-research"]) == (
        "my-research",
        "notes",
    )


def test_split_project_permalink_prefix_does_not_swallow_a_same_named_note():
    """A note whose file stem equals its project's name must not be misread as
    the project itself with no remainder (#1458). Extension-stripping is only
    safe when a remainder still follows the claimed segments; a match that
    would leave nothing after it must spell the permalink exactly, extension
    included, or it is a note living at the project's root, not the project."""
    permalinks = ["moby-dick"]

    assert split_project_permalink_prefix("moby-dick.txt", permalinks) is None
    # A real path under the project still strips the extension-free segment.
    assert split_project_permalink_prefix("moby-dick/moby-dick.txt", permalinks) == (
        "moby-dick",
        "moby-dick.txt",
    )
    # The bare project name (no extension to mistakenly drop) still claims the
    # whole path with an empty remainder.
    assert split_project_permalink_prefix("moby-dick", permalinks) == ("moby-dick", "")


@pytest.mark.asyncio
async def test_workspace_resolvers_reject_non_routes_without_discovery():
    """Both entry points refuse shapes that cannot be a workspace route before
    touching discovery — no workspace index is built for a plain memory URL or a
    single-segment identifier, which is what keeps read_note and search off the
    cloud round trip."""
    assert await resolve_workspace_qualified_memory_url("second-project/notes/x") is None
    assert await resolve_workspace_qualified_identifier("single-segment") is None


def test_agreed_route_project_across_mixed_qualification():
    """A workspace-qualified spelling agrees with the unqualified spelling of
    the same project, in either direction, and the more-qualified one carries
    the route; different projects never agree.

    This is the fallback path, reached only when the explicit spelling names no
    project this session addresses — so there is no identity to compare and the
    shapes are all there is. Then segment count decides, not the mere presence
    of a slash: a workspace slug is exactly one segment, so 'acme/Research/2026'
    qualifies the project 'Research/2026' while it does not qualify a project
    named '2026'.
    """
    outside = ()

    assert _agreed_route_project("research", "other/research", outside) == "other/research"
    assert _agreed_route_project("other/research", "research", outside) == "other/research"
    assert _agreed_route_project("second-project", "other/research", outside) is None

    # Slash-bearing project names: the escape hatch has to keep working.
    assert (
        _agreed_route_project("Research/2026", "acme/Research/2026", outside)
        == "acme/Research/2026"
    )
    # ...without agreeing with a different project that merely shares a tail.
    assert _agreed_route_project("2026", "acme/Research/2026", outside) is None
    # A mount named after a workspace still conflicts with that workspace route.
    assert _agreed_route_project("team", "team/docs", outside) is None


def test_agreed_route_project_prefers_identity_over_shape():
    """When the explicit spelling names a project this session addresses, the
    answer comes from identity and the shapes are never consulted.

    'team/docs' beside 'docs' are two real projects, not one project spelled two
    ways. Reading the extra leading segment as a workspace qualifier made them
    agree, so an explicit project='docs' was silently discarded and the call read
    the other project instead of conflicting.
    """
    addressable = (
        AddressableProject(name="docs", permalink="docs"),
        AddressableProject(name="team/docs", permalink="team/docs"),
    )

    assert _agreed_route_project("team/docs", "docs", addressable) is None
    assert _agreed_route_project("docs", "team/docs", addressable) is None

    # The same project under two spellings still agrees, by permalink equality.
    assert _agreed_route_project("Team/Docs", "team/docs", addressable) == "Team/Docs"

    # With no such project addressable, the shape fallback still applies.
    assert _agreed_route_project("team/docs", "docs", ()) == "team/docs"


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
    """What a stood-up cloud session exposes to a test.

    ``tenant`` is the live listing the fake client serves, so a test can add or
    remove a project the way create_memory_project/delete_project do and then
    assert what the session sees next. ``projects`` is the set it started with.
    """

    projects: tuple[ProjectItem, ...]
    listings: list[ProjectList]
    tenant: list[ProjectItem]


def _routes_to_project(route: ProjectPathRoute, permalink: str) -> bool:
    """True when a route landed on the project the mount view advertises.

    Cloud routes may come back workspace-qualified ('team/research'), so accept
    either the bare permalink or that permalink behind exactly one workspace
    segment — the same rule the resolver uses to compare two spellings.
    """
    assert route.project is not None
    routed = generate_permalink(route.project)
    return routed == permalink or (
        routed.endswith(f"/{permalink}") and routed.count("/") - permalink.count("/") == 1
    )


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
        tenant = list(projects)
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
            project_list = ProjectList(projects=list(tenant), default_project=default)
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
        return _CloudSession(projects=projects, listings=listings, tenant=tenant)

    return build


# --- project lifecycle inside one session ---
# The session's project listing is memoized per MCP request, and a hosted
# session's request can outlive a project lifecycle change: an agent calls
# `ls "/"`, then create_memory_project, then addresses the new project. That
# snapshot decides which first segment names a project, so it has to be part of
# what "a project changed" invalidates (#1432 review).


# --- out-of-band project changes ---
# Invalidation covers what this session did; the age bound covers what it did
# not. `session_project_list` is session state — fastmcp keys set_state by
# session id and persists it across tool calls — so without a bound a project a
# teammate or the CLI created stayed invisible until the session ended.


async def _age_out_session_project_list(context: ContextState) -> None:
    """Make the stored snapshot older than the bound, without touching clocks.

    The bound is read off a stored fetch time, so moving that timestamp is the
    honest way to simulate elapsed time: it exercises the real comparison rather
    than a patched clock.
    """
    await context.set_state(
        project_context._SESSION_PROJECT_LIST_FETCHED_AT_STATE_KEY,
        time.time() - project_context._SESSION_PROJECT_LIST_MAX_AGE_SECONDS - 1,
    )


@pytest.mark.asyncio
async def test_out_of_band_project_becomes_addressable_when_the_snapshot_ages_out(
    cloud_session,
):
    """A project this session did not create is reachable without a restart.

    Nothing calls invalidate_project_caches here — that is the point. The
    snapshot simply ages out, and the mount view, the posix resolver and
    identifier detection all pick the project up together, because the bound
    lives in the loader they share.
    """
    session = cloud_session("research", default="research")
    context = ContextState()
    config = ConfigManager().config

    advertised = await ls("/", context=ctx(context))
    assert {node["name"] for node in advertised["nodes"]} == {"research"}

    session.tenant.append(
        ProjectItem(
            id=len(session.tenant) + 1,
            external_id="new-project-external-id",
            name="new-project",
            path="/app/data/new-project",
            is_default=False,
        )
    )

    # Still inside the bound: the snapshot is authoritative and nothing refetches.
    assert (
        await detect_project_from_identifier_prefix(
            "new-project/notes/x", config, context=ctx(context)
        )
        is None
    )

    await _age_out_session_project_list(context)

    advertised = await ls("/", context=ctx(context))
    assert {node["name"] for node in advertised["nodes"]} == {"new-project", "research"}
    assert await detect_project_from_identifier_prefix(
        "new-project/notes/x", config, context=ctx(context)
    ) == DetectedProjectRoute(project="new-project", project_id="new-project-external-id")
    route = await resolve_project_path_route(
        "new-project/notes/x", project=None, project_id=None, context=ctx(context)
    )
    assert route.project == "new-project"


@pytest.mark.asyncio
async def test_a_non_project_identifier_costs_one_listing_per_interval(cloud_session):
    """The bound the age gate buys: misses cannot drive the refetch rate.

    A miss is the ordinary answer for a path-shaped identifier that is not a
    project reference, so a refresh triggered by misses would spend a listing
    per lookup on the hottest path in the tool surface. Age cannot be influenced
    by what the caller asks for: five identical misses across an expired
    snapshot cost exactly one refetch.
    """
    session = cloud_session("research", default="research")
    context = ContextState()
    config = ConfigManager().config

    # Warm both snapshots, so what is counted below is the session listing only
    # and not the workspace index the rule-3 fall-through builds once.
    assert (
        await detect_project_from_identifier_prefix(
            "specs/search-spec", config, context=ctx(context)
        )
        is None
    )
    session.listings.clear()

    await _age_out_session_project_list(context)
    for _ in range(5):
        assert (
            await detect_project_from_identifier_prefix(
                "specs/search-spec", config, context=ctx(context)
            )
            is None
        )

    assert len(session.listings) == 1


@pytest.mark.asyncio
async def test_project_created_mid_session_is_addressable_without_a_restart(cloud_session):
    """A project created after the mount snapshot is addressable immediately.

    invalidate_project_caches cleared the active project and the workspace
    index, but not the session listing, so `ls "/"` kept advertising the old
    mount table and a plain '<new-project>/path' prefix went undetected —
    falling through to the default project instead.
    """
    session = cloud_session("research", default="research")
    context = ContextState()
    config = ConfigManager().config

    advertised = await ls("/", context=ctx(context))
    assert {node["name"] for node in advertised["nodes"]} == {"research"}

    # What create_memory_project does: the tenant serves the new project, and
    # the tool invalidates this session's project caches.
    session.tenant.append(
        ProjectItem(
            id=len(session.tenant) + 1,
            external_id="new-project-external-id",
            name="new-project",
            path="/app/data/new-project",
            is_default=False,
        )
    )
    await invalidate_project_caches(ctx(context))

    advertised = await ls("/", context=ctx(context))
    assert {node["name"] for node in advertised["nodes"]} == {"new-project", "research"}

    route = await resolve_project_path_route(
        "new-project/notes/x", project=None, project_id=None, context=ctx(context)
    )
    assert route == ProjectPathRoute(
        project="new-project",
        path="notes/x",
        stripped=True,
        project_id="new-project-external-id",
    )

    detected = await detect_project_from_identifier_prefix(
        "new-project/notes/x", config, context=ctx(context)
    )
    assert detected == DetectedProjectRoute(
        project="new-project", project_id="new-project-external-id"
    )


@pytest.mark.asyncio
async def test_project_deleted_mid_session_stops_resolving(cloud_session):
    """A deleted project stops being a route rather than keeping its dead id.

    The stale snapshot kept answering with the removed project's external_id,
    so a plain prefix routed to a UUID the tenant no longer serves.
    """
    session = cloud_session("research", "doomed", default="research")
    context = ContextState()
    config = ConfigManager().config

    assert await detect_project_from_identifier_prefix(
        "doomed/notes/x", config, context=ctx(context)
    ) == DetectedProjectRoute(project="doomed", project_id="doomed-external-id")

    session.tenant[:] = [item for item in session.tenant if item.name != "doomed"]
    await invalidate_project_caches(ctx(context))

    assert (
        await detect_project_from_identifier_prefix("doomed/notes/x", config, context=ctx(context))
        is None
    )
    advertised = await ls("/", context=ctx(context))
    assert {node["name"] for node in advertised["nodes"]} == {"research"}


@pytest.mark.asyncio
async def test_workspace_metadata_survives_a_project_lifecycle_change(cloud_session):
    """Only project-shaped caches are cleared.

    ``active_workspace`` and ``available_workspaces`` hold tenant id, slug and
    type — which no project create or delete changes, and which no MCP tool can
    change at all, since none creates or deletes a workspace. Dropping them
    would cost a control-plane round trip per project change for nothing.
    """
    cloud_session("research", default="research")
    context = ContextState()
    workspace = WorkspaceInfo(
        tenant_id="team-tenant",
        workspace_type="organization",
        slug="team",
        name="Team",
        role="editor",
        is_default=True,
    )
    await context.set_state("active_workspace", workspace.model_dump())
    await context.set_state("available_workspaces", [workspace.model_dump()])

    await invalidate_project_caches(ctx(context))

    assert await context.get_state("active_workspace") == workspace.model_dump()
    assert await context.get_state("available_workspaces") == [workspace.model_dump()]
    # ...while every cache that holds projects is gone.
    assert await context.get_state("active_project") is None
    assert await context.get_state("default_project_name") is None
    assert await context.get_state("workspace_project_index") is None
    assert await context.get_state("session_project_list") is None


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
    assert _routes_to_project(route, "research")


@pytest.mark.asyncio
async def test_cloud_workspace_qualified_path_without_mount_collision_still_routes(cloud_session):
    """No project is named 'team', so the workspace slug is free to claim the
    first segment: '<workspace>/<project>/<path>' resolves through workspace
    discovery, qualified name and all. Mount precedence takes nothing away from
    the spellings that address other workspaces."""
    cloud_session("research", "engineering")

    route = await resolve_project_path_route("team/research/notes/x", project=None, project_id=None)

    # The route carries the resolved entry's id, so nothing re-resolves the
    # qualified name against a workspace that did not answer it.
    assert route == ProjectPathRoute(
        project="team/research",
        path="notes/x",
        stripped=True,
        project_id="research-external-id",
    )


@pytest.mark.asyncio
async def test_local_cloud_session_lists_a_workspace_project_root(
    multi_project_config, monkeypatch
):
    """The pathless root gets the same discovery permission as the path form.

    In a locally routed session holding cloud credentials, 'acme/docs/note'
    resolved through workspace discovery while 'acme/docs' — that same project's
    root, and the thing `ls` needs to enter it — refused. The stricter
    three-segment gate belongs to identifier detection (read_note, search),
    which has no mount table to decline first and no refusal rule behind it.
    """
    workspace = WorkspaceInfo(
        tenant_id="acme-tenant",
        workspace_type="organization",
        slug="acme",
        name="Acme",
        role="editor",
        is_default=True,
    )

    async def fake_workspaces(context=None) -> list[WorkspaceInfo]:
        return [workspace]

    async def fake_entries(ws, context=None):
        return (
            WorkspaceProjectEntry(
                workspace=ws,
                project=ProjectItem(
                    id=1,
                    external_id="99999999-9999-9999-9999-999999999999",
                    name="docs",
                    path="/app/data/docs",
                ),
            ),
        )

    monkeypatch.setattr(project_context, "get_available_workspaces", fake_workspaces)
    monkeypatch.setattr(project_context, "_fetch_workspace_project_entries", fake_entries)
    monkeypatch.setattr(project_context, "has_cloud_credentials", lambda config: True)

    root = await resolve_project_path_route("acme/docs", project=None, project_id=None)
    nested = await resolve_project_path_route("acme/docs/note", project=None, project_id=None)

    assert root == ProjectPathRoute(
        project="acme/docs",
        path="",
        stripped=True,
        project_id="99999999-9999-9999-9999-999999999999",
    )
    assert nested.project == "acme/docs"
    assert nested.path == "note"


@pytest.mark.asyncio
async def test_cloud_workspace_qualified_project_root_routes(cloud_session):
    """'<workspace>/<project>' with no path names that project's root, exactly as
    a bare mount name does. Only the three-segment form used to resolve, so a
    cross-workspace project could be listed into ('acme/docs/notes') but its own
    root ('acme/docs') had no spelling at all — it fell through to the
    multi-project refusal."""
    cloud_session("research", "engineering")

    route = await resolve_project_path_route("team/research", project=None, project_id=None)

    assert route == ProjectPathRoute(
        project="team/research", path="", stripped=True, project_id="research-external-id"
    )


@pytest.mark.asyncio
async def test_cross_workspace_extension_bearing_project_name_routes_root(cloud_session):
    """Workspace discovery preserves exact display names whose extension is
    absent from the stored permalink."""
    cloud_session("research", "engineering", "docs.txt")

    route = await resolve_project_path_route("team/docs.txt", project=None, project_id=None)

    assert route == ProjectPathRoute(
        project="team/docs", path="", stripped=True, project_id="docs-external-id"
    )


@pytest.mark.asyncio
async def test_cross_workspace_extension_project_root_accepts_normalized_spelling(cloud_session):
    """Workspace display-name matching retains the usual case and spacing normalization."""
    cloud_session("research", "engineering", "My Docs.txt")

    route = await resolve_project_path_route("team/my-docs.txt", project=None, project_id=None)

    assert route == ProjectPathRoute(
        project="team/my-docs", path="", stripped=True, project_id="my-docs-external-id"
    )


@pytest.mark.asyncio
async def test_explicit_cross_workspace_extension_project_name_routes_root(cloud_session):
    """An explicit qualified display name makes its extension-bearing root unambiguous."""
    cloud_session("research", "engineering", "docs.txt")

    route = await resolve_project_path_route(
        "team/docs.txt", project="team/docs.txt", project_id=None
    )

    assert route == ProjectPathRoute(project="team/docs.txt", path="", stripped=True)


@pytest.mark.asyncio
async def test_cloud_workspace_route_matches_multi_segment_project_permalink(cloud_session):
    """A workspace route splits project from path by matching that workspace's
    project permalinks, not by taking one segment. The mount table learned this
    first; the workspace parser rediscovered the same wrong assumption, so both
    now go through split_project_permalink_prefix and neither can drift."""
    cloud_session("Research/2026", "research")

    nested = await resolve_project_path_route(
        "team/research/2026/notes", project=None, project_id=None
    )
    assert nested == ProjectPathRoute(
        project="team/research/2026",
        path="notes",
        stripped=True,
        project_id="research/2026-external-id",
    )

    root = await resolve_project_path_route("team/research/2026", project=None, project_id=None)
    assert root == ProjectPathRoute(
        project="team/research/2026",
        path="",
        stripped=True,
        project_id="research/2026-external-id",
    )

    # The shorter sibling still claims its own paths — longest match, not first.
    sibling = await resolve_project_path_route("team/research/notes", project=None, project_id=None)
    assert sibling == ProjectPathRoute(
        project="team/research",
        path="notes",
        stripped=True,
        project_id="research-external-id",
    )


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
async def test_cloud_slash_bearing_project_resolves_by_its_own_name(cloud_session):
    """Resolving a project identifier tries the whole name before reading its
    first segment as a workspace.

    'Research/2026' and 'acme/docs' are the same shape, so a slash-bearing
    project name used to be unroutable — the lookup took 'Research' for a
    workspace and failed with "Workspace 'Research' was not found". The v2
    project router already resolved exact-first for this reason; the index now
    matches it.
    """
    cloud_session("Research/2026", "engineering")

    entry = await resolve_workspace_project_identifier("Research/2026")

    assert entry.project.name == "Research/2026"
    assert entry.qualified_name == "team/research/2026"

    # The workspace-qualified spelling still resolves through the split.
    qualified = await resolve_workspace_project_identifier("team/Research/2026")
    assert qualified.project.name == "Research/2026"


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
        assert _routes_to_project(root_route, permalink)

        path_route = await resolve_project_path_route(
            f"{permalink}/notes/x", project=None, project_id=None
        )
        assert path_route.stripped is True
        assert path_route.path == "notes/x"
        assert _routes_to_project(path_route, permalink)


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

    assert _routes_to_project(route, "beta")


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
async def test_emitted_cross_workspace_path_replays_with_one_mount(cross_workspace_session):
    """A path this layer emits must route back through the same session.

    `ls("docs", project="acme/docs")` strips the agreeing prefix and requalifies
    its children as 'acme/docs/...'. Replaying one without the project argument
    used to hit the mount-count gate, skip workspace parsing, and read the sole
    mount's same-named path in the *other* tenant — a navigation path returned
    for one workspace silently reading another.
    """
    cross_workspace_session(session_projects=("docs",), default_projects=("docs",))

    original = await resolve_project_path_route("docs", project="acme/docs", project_id=None)
    assert original.project == "acme/docs"

    replay = await resolve_project_path_route("acme/docs/notes", project=None, project_id=None)

    assert replay == ProjectPathRoute(
        project="acme/docs", path="notes", stripped=True, project_id=_DEFAULT_DOCS_ID
    )


@pytest.mark.asyncio
async def test_workspace_shaped_path_routes_even_with_one_mount(cross_workspace_session):
    """Route versus path, decided by the precedence order rather than a parse.

    An unqualified path whose leading segments name a real accessible workspace
    and a real project inside it is read as a route, whatever the session
    mounts. This briefly depended on the mount count, to keep a coincidentally
    workspace-shaped relative path at home in a one-mount session — but that
    made the qualified paths this layer itself emits unroutable there, and both
    readings are a silent wrong-project read. The tie breaks on whose string it
    is: the emitted canonical form is ours and must route back; a user-typed
    collision has the explicit fix asserted in the test below.
    """
    cross_workspace_session(session_projects=("research",), default_projects=("docs",))

    route = await resolve_project_path_route("acme/docs/foo", project=None, project_id=None)

    assert route == ProjectPathRoute(
        project="acme/docs", path="foo", stripped=True, project_id=_DEFAULT_DOCS_ID
    )

    # A path that only looks workspace-shaped matches no workspace and stays put.
    coincidence = await resolve_project_path_route("notes/2026/foo", project=None, project_id=None)
    assert coincidence == ProjectPathRoute(project=None, path="notes/2026/foo", stripped=False)


@pytest.mark.asyncio
async def test_named_project_strips_its_own_qualified_prefix(cross_workspace_session):
    """Rule 2 — an agreeing prefix strips — has to hold for the qualified
    spelling too, and it is decidable without any network.

    Only rule 4's discovery used to recognize a workspace-qualified project in
    the path, and the precedence order now declines to run that when a project
    was named. So the caller's own two spellings of one project stopped
    agreeing: 'foo' was read as 'acme/docs/foo'. It also broke the round trip,
    since a routed ls("acme/docs") returns exactly this prefixed form.
    """
    cross_workspace_session(session_projects=("research",), default_projects=("docs",))

    route = await resolve_project_path_route("acme/docs/foo", project="acme/docs", project_id=None)

    assert route == ProjectPathRoute(project="acme/docs", path="foo", stripped=True)

    # A prefix that is NOT the named project still stays part of the path.
    unrelated = await resolve_project_path_route(
        "acme/docs/foo", project="research", project_id=None
    )
    assert unrelated.path == "acme/docs/foo"


@pytest.mark.asyncio
async def test_named_project_keeps_the_rest_of_the_path_inside_it(cross_workspace_session):
    """An explicit project settles route-versus-path: the caller said which
    project they mean, so the remaining path is inside it and nothing reroutes.

    This spelling previously raised a prefix conflict, which left the reported
    bug with no workaround at all — the path could neither stay home on its own
    nor be pinned there.
    """
    cross_workspace_session(session_projects=("research",), default_projects=("docs",))

    route = await resolve_project_path_route("acme/docs/foo", project="research", project_id=None)

    assert route == ProjectPathRoute(project="research", path="acme/docs/foo", stripped=False)


@pytest.mark.asyncio
async def test_workspace_route_still_wins_when_the_path_could_not_resolve(
    cross_workspace_session,
):
    """The other half of the order: with several projects mounted, an
    unqualified path refuses anyway, so reading the leading segments as a route
    is the only way the input can mean anything. Cross-workspace addressing must
    not be lost to the fix above."""
    cross_workspace_session(
        session_projects=("research", "engineering"), default_projects=("docs",)
    )

    route = await resolve_project_path_route("acme/docs/foo", project=None, project_id=None)

    assert route == ProjectPathRoute(
        project="acme/docs", path="foo", stripped=True, project_id=_DEFAULT_DOCS_ID
    )

    # And an unaddressable name still refuses rather than defaulting.
    with pytest.raises(UnqualifiedPathRefusedError):
        await resolve_project_path_route("nope/nothing/x", project=None, project_id=None)


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
    through to a refusal that claims the project does not exist.

    Two mounted projects, because workspace routes are only parsed when an
    unqualified path could not resolve anyway — see the precedence order.
    """
    cross_workspace_session(
        failed_tenant="default-tenant",
        session_projects=("research", "engineering"),
    )

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
    assert qualified_path == ProjectPathRoute(
        project="acme/notes", path="foo", stripped=True, project_id=_DEFAULT_NOTES_ID
    )

    root = await resolve_project_path_route("acme/notes", project=None, project_id=None)
    assert root == ProjectPathRoute(
        project="acme/notes", path="", stripped=True, project_id=_DEFAULT_NOTES_ID
    )

    explicit = await resolve_project_path_route("foo", project="acme/notes", project_id=None)
    assert explicit == ProjectPathRoute(project="acme/notes", path="foo", stripped=False)


# --- a qualified name must reach the workspace it names (#1421) ---


def _index_with(*entries: tuple[WorkspaceInfo, str, str]) -> WorkspaceProjectIndex:
    """Build a workspace index from (workspace, project name, external_id) rows."""
    by_tenant: dict[str, WorkspaceInfo] = {}
    for workspace, _, _ in entries:
        by_tenant.setdefault(workspace.tenant_id, workspace)
    workspaces = tuple(by_tenant.values())
    return build_workspace_project_index(
        workspaces,
        tuple(
            WorkspaceProjectEntry(
                workspace=workspace,
                project=ProjectItem(
                    id=index + 1,
                    external_id=external_id,
                    name=name,
                    path=f"/app/data/{generate_permalink(name)}",
                ),
            )
            for index, (workspace, name, external_id) in enumerate(entries)
        ),
    )


@pytest.mark.asyncio
async def test_slug_precedence_survives_the_whole_permalink_probe():
    """One workspace's display name must not shadow another's slug.

    match_workspace_identifier gives slugs global precedence. The probe that
    decides whether the qualified reading resolves had its own inline matcher
    that took the first field to hit in iteration order, so a display-name
    workspace won, the qualified reading looked like a miss, and the request
    fell through to an unrelated whole-permalink project in a third workspace —
    a cross-workspace read from a route that named a real slug.
    """
    named_foo = WorkspaceInfo(
        tenant_id="t1",
        workspace_type="organization",
        slug="wsone",
        name="foo",
        role="editor",
        is_default=False,
    )
    slug_foo = WorkspaceInfo(
        tenant_id="t2",
        workspace_type="organization",
        slug="foo",
        name="Second",
        role="editor",
        is_default=False,
    )
    third = WorkspaceInfo(
        tenant_id="t3",
        workspace_type="organization",
        slug="third",
        name="Third",
        role="editor",
        is_default=False,
    )
    index = build_workspace_project_index(
        (named_foo, slug_foo, third),
        (
            WorkspaceProjectEntry(
                workspace=slug_foo,
                project=ProjectItem(
                    id=1,
                    external_id="11111111-1111-1111-1111-111111111111",
                    name="docs",
                    path="/app/docs",
                ),
            ),
            WorkspaceProjectEntry(
                workspace=third,
                project=ProjectItem(
                    id=2,
                    external_id="22222222-2222-2222-2222-222222222222",
                    name="foo/docs",
                    path="/app/foo-docs",
                ),
            ),
        ),
    )

    entry = await resolve_workspace_project_from_index(index, "foo/docs")

    assert entry.workspace.slug == "foo"
    assert entry.project.name == "docs"


@pytest.mark.asyncio
async def test_qualified_name_beats_a_colliding_whole_permalink():
    """'<workspace>/<project>' and a project literally named 'acme/docs' are the
    same shape, so the index tries both readings — qualified first.

    Preferring the whole permalink sent a route that explicitly named workspace
    'acme' to a *different* tenant that happened to hold a project called
    'acme/docs'. The fallback still exists: it is what makes a slash-bearing
    name routable when its first segment names no workspace at all.
    """
    acme = WorkspaceInfo(
        tenant_id="acme-tenant",
        workspace_type="organization",
        slug="acme",
        name="Acme",
        role="editor",
        is_default=True,
    )
    beta = WorkspaceInfo(
        tenant_id="beta-tenant",
        workspace_type="organization",
        slug="beta",
        name="Beta",
        role="editor",
        is_default=False,
    )
    index = _index_with(
        (acme, "docs", "11111111-1111-1111-1111-111111111111"),
        (beta, "acme/docs", "22222222-2222-2222-2222-222222222222"),
        (beta, "Research/2026", "33333333-3333-3333-3333-333333333333"),
    )

    qualified = await resolve_workspace_project_from_index(index, "acme/docs")
    assert qualified.workspace.slug == "acme"
    assert qualified.project.name == "docs"

    # The fallback: no workspace is named 'Research', so the whole permalink wins.
    slash_bearing = await resolve_workspace_project_from_index(index, "Research/2026")
    assert slash_bearing.workspace.slug == "beta"
    assert slash_bearing.project.name == "Research/2026"

    # And the collided project is still reachable by the id that names it exactly.
    by_id = await resolve_workspace_project_from_index(
        index, "22222222-2222-2222-2222-222222222222"
    )
    assert by_id.project.name == "acme/docs"


@pytest.mark.asyncio
async def test_empty_session_workspace_still_reaches_a_named_workspace(monkeypatch, config_manager):
    """Route-versus-path protects *exactly* one addressable project, not none.

    A factory session whose connection-time workspace holds no projects has no
    project for a relative path to resolve inside, and an empty mount table
    cannot refuse on the caller's behalf either — so 'acme/docs/note' fell
    through to project=None and tried the empty workspace. With nothing to take
    away, the explicitly named workspace wins.
    """
    config = config_manager.load_config()
    config.projects = {}
    config.default_project = None
    config_manager.save_config(config)

    empty_ws = WorkspaceInfo(
        tenant_id="empty-tenant",
        workspace_type="organization",
        slug="beta",
        name="Beta",
        role="editor",
        is_default=False,
    )
    acme = WorkspaceInfo(
        tenant_id="acme-tenant",
        workspace_type="organization",
        slug="acme",
        name="Acme",
        role="editor",
        is_default=True,
    )

    @asynccontextmanager
    async def fake_get_client(*a, **k):
        yield object()

    async def fake_list_projects(self):
        return ProjectList(projects=[], default_project=None)  # session workspace is EMPTY

    async def fake_workspaces(context=None):
        return [empty_ws, acme]

    async def fake_entries(ws, context=None):
        if ws.tenant_id != "acme-tenant":
            return ()
        return (
            WorkspaceProjectEntry(
                workspace=ws,
                project=ProjectItem(
                    id=1,
                    external_id="77777777-7777-7777-7777-777777777777",
                    name="docs",
                    path="/app/docs",
                ),
            ),
        )

    monkeypatch.setattr(async_client, "is_factory_mode", lambda: True)
    monkeypatch.setattr(async_client, "get_client", fake_get_client)
    monkeypatch.setattr(
        "basic_memory.mcp.clients.project.ProjectClient.list_projects", fake_list_projects
    )
    monkeypatch.setattr(project_context, "get_available_workspaces", fake_workspaces)
    monkeypatch.setattr(project_context, "_fetch_workspace_project_entries", fake_entries)

    route = await resolve_project_path_route("acme/docs/note", project=None, project_id=None)

    assert route == ProjectPathRoute(
        project="acme/docs",
        path="note",
        stripped=True,
        project_id="77777777-7777-7777-7777-777777777777",
    )
