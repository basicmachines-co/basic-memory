"""Direct unit tests for resolve_project_path_route (#1415).

The resolver is the single routing seam for the posix tools: '<project>/path'
inputs route by first segment, an explicit project must agree with a path
prefix, and multi-project configs refuse unqualified input instead of
defaulting. These tests drive the function branch-by-branch against configs
written through the test ConfigManager; no API client is involved because
local-config matching never leaves the process.
"""

import re

import pytest

from basic_memory.config_models import ProjectEntry
from basic_memory.mcp.project_context import (
    ProjectPathRoute,
    ProjectPrefixConflictError,
    UnqualifiedPathRefusedError,
    _detected_route_remainder,
    _project_routes_agree,
    resolve_project_path_route,
)


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
    """A cloud-only local client (no local mount table) keeps API-side default
    resolution — refusal needs a config that can enumerate projects."""
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
