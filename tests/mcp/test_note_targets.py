"""Routing a bm_* target into its project and resolving notes exactly (#1829).

These run the real ASGI stack through the shared `client` fixture.
"""

import json
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from basic_memory.mcp.clients import KnowledgeClient
from basic_memory.mcp.note_targets import (
    PathQualifier,
    hint_project_ref,
    open_project_target,
    resolve_note,
)
from basic_memory.mcp.project_context import ProjectPathRoute
from basic_memory.mcp.tools import bm_write
from basic_memory.schemas.project_info import ProjectItem


async def _write_plan(project_name: str) -> None:
    await bm_write(
        title="Plan",
        directory="notes",
        content="# Plan\n\nship it",
        project=project_name,
    )


@pytest.mark.asyncio
async def test_resolves_a_note_by_path_in_the_named_project(client, test_project):
    await _write_plan(test_project.name)

    async with open_project_target("notes/Plan.md", test_project.name, None) as target:
        note = await resolve_note(target, "notes/Plan.md")

    assert target.project.name == test_project.name
    assert target.route.path == "notes/Plan.md"
    assert note.file_path == "notes/Plan.md"
    assert note.external_id


@pytest.mark.asyncio
async def test_project_external_id_routes_like_the_name(client, test_project):
    await _write_plan(test_project.name)

    async with open_project_target("Plan", test_project.external_id, None) as target:
        note = await resolve_note(target, "Plan")

    assert target.project.external_id == test_project.external_id
    assert note.title == "Plan"


@pytest.mark.asyncio
async def test_project_prefix_on_the_target_routes_without_a_project_argument(client, test_project):
    await _write_plan(test_project.name)

    async with open_project_target(f"{test_project.name}/notes/Plan.md", None, None) as target:
        await resolve_note(target, f"{test_project.name}/notes/Plan.md")

    assert target.route.stripped
    assert target.route.path == "notes/Plan.md"


@pytest.mark.asyncio
async def test_a_missing_note_is_a_not_found_refusal(client, test_project):
    async with open_project_target("notes/Nope.md", test_project.name, None) as target:
        with pytest.raises(ToolError) as refused:
            await resolve_note(target, "notes/Nope.md")

    message = str(refused.value)
    assert message.startswith("NOT_FOUND: No note matches 'notes/Nope.md'")
    assert f"bm_search(query='notes/Nope.md', project='{test_project.name}')" in message


@pytest.mark.asyncio
async def test_a_missing_note_refusal_in_json(client, test_project):
    async with open_project_target("notes/Nope.md", test_project.name, None) as target:
        with pytest.raises(ToolError) as refused:
            await resolve_note(target, "notes/Nope.md", output_format="json")

    payload = json.loads(str(refused.value))
    assert payload["error"] == "NOT_FOUND"
    assert payload["try_instead"] == (
        f"bm_search(query='notes/Nope.md', project='{test_project.name}')"
    )


@pytest.mark.asyncio
async def test_a_bare_project_name_is_not_a_note(client, test_project):
    identifier = f"{test_project.name}/"
    async with open_project_target(identifier, None, None) as target:
        with pytest.raises(ToolError) as refused:
            await resolve_note(target, identifier)

    message = str(refused.value)
    assert message.startswith("INVALID_ARGUMENTS:")
    assert f"bm_list(path='/', project='{test_project.name}')" in message


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["../escape.md", "notes/../../escape.md", "~/notes/plan.md"])
async def test_targets_outside_the_project_are_refused(client, test_project, target):
    with pytest.raises(ToolError) as refused:
        async with open_project_target(target, test_project.name, None):
            pass  # pragma: no cover — the refusal happens before the body

    assert str(refused.value).startswith("OUTSIDE_PROJECT:")


@pytest.mark.asyncio
async def test_outside_project_refusal_in_json(client, test_project):
    with pytest.raises(ToolError) as refused:
        async with open_project_target(
            "../escape.md", test_project.name, None, output_format="json"
        ):
            pass  # pragma: no cover — the refusal happens before the body

    assert json.loads(str(refused.value))["error"] == "OUTSIDE_PROJECT"


@pytest.mark.asyncio
async def test_a_symlink_out_of_a_local_project_is_refused(client, test_project, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (Path(test_project.path) / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ToolError) as refused:
        async with open_project_target("escape/secret.md", test_project.name, None):
            pass  # pragma: no cover — the refusal happens before the body

    assert str(refused.value).startswith("OUTSIDE_PROJECT:")


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["/", "/notes", "notes/Plan.md", "memory://notes/plan"])
async def test_project_relative_targets_are_allowed(client, test_project, target):
    # A leading '/' anchors at the project root, not the filesystem root.
    async with open_project_target(target, test_project.name, None) as routed:
        assert routed.project.name == test_project.name


@pytest.mark.asyncio
async def test_other_resolution_failures_pass_through(client, test_project, monkeypatch):
    # A failure that is not a 404 (here: no HTTP cause at all) must not be reported
    # as "not found".
    async def failing_resolve(self, identifier, *, strict=False):
        raise ToolError("backend unavailable")

    monkeypatch.setattr(KnowledgeClient, "resolve_entity_response", failing_resolve)

    async with open_project_target("notes/Plan.md", test_project.name, None) as target:
        with pytest.raises(ToolError, match="backend unavailable"):
            await resolve_note(target, "notes/Plan.md")


@pytest.mark.asyncio
async def test_an_identifier_can_be_a_title_that_looks_like_a_path(client, test_project):
    """A title such as 'Home ~ Plan' is resolved, not checked as a path."""
    await bm_write(
        title="Home ~ Plan", directory="notes", content="# Home", project=test_project.name
    )

    async with open_project_target(
        "Home ~ Plan", test_project.name, None, kind="identifier"
    ) as target:
        note = await resolve_note(target, "Home ~ Plan")

    assert note.title == "Home ~ Plan"
    with pytest.raises(ToolError, match="^OUTSIDE_PROJECT:"):
        async with open_project_target("~/x", test_project.name, None):
            pass


@pytest.mark.asyncio
async def test_an_ambiguous_title_is_an_ambiguous_refusal(client, test_project):
    """Two notes share the title, so neither is picked; JSON keeps the refusal shape."""
    for directory in ("a", "b"):
        await bm_write(
            title="Twin", directory=directory, content="# Twin", project=test_project.name
        )

    async with open_project_target("Twin", test_project.name, None, kind="identifier") as target:
        with pytest.raises(ToolError) as refused:
            await resolve_note(target, "Twin", output_format="json")

    assert json.loads(str(refused.value))["error"] == "AMBIGUOUS"


@pytest.mark.asyncio
async def test_routing_refusals_keep_the_requested_format(client, test_project, second_project):
    """Routing's own refusals come back in the refusal shape, JSON included."""
    with pytest.raises(ToolError) as conflict:
        async with open_project_target(
            f"{test_project.permalink}/notes/a.md", second_project.name, None, output_format="json"
        ):
            pass
    with pytest.raises(ToolError) as unqualified:
        async with open_project_target("no-such-folder/a.md", None, None, output_format="json"):
            pass

    assert json.loads(str(conflict.value))["error"] == "INVALID_ARGUMENTS"
    assert json.loads(str(unqualified.value))["error"] == "INVALID_ARGUMENTS"


# --- PathQualifier ---


def _route(*, stripped: bool, project: str | None = "My Research") -> ProjectPathRoute:
    return ProjectPathRoute(project=project, project_id=None, path="notes", stripped=stripped)


def test_qualifier_without_a_stripped_prefix_changes_nothing():
    qualifier = PathQualifier.for_route(_route(stripped=False))

    assert qualifier.prefix is None
    assert qualifier.path("notes/a.md") == "notes/a.md"
    assert qualifier.note({"file_path": "a.md"}) == {"file_path": "a.md"}


@pytest.mark.parametrize(
    ("value", "qualified"),
    [
        ("notes/a.md", "my-research/notes/a.md"),
        ("/notes", "/my-research/notes"),
        ("/", "/my-research"),
        ("", "my-research"),
    ],
)
def test_qualifier_keeps_the_slash_shape(value, qualified):
    assert PathQualifier.for_route(_route(stripped=True)).path(value) == qualified


def test_qualifier_rewrites_addresses_not_frontmatter():
    qualifier = PathQualifier(prefix="research")
    note = {"file_path": "a.md", "frontmatter": {"file_path": "kept.md"}, "permalink": "a"}

    assert qualifier.note(note) == {
        "file_path": "research/a.md",
        "frontmatter": {"file_path": "kept.md"},
        "permalink": "a",
    }
    assert qualifier.search({"results": [{"file_path": "a.md", "permalink": "a"}]}) == {
        "results": [{"file_path": "research/a.md", "permalink": "a"}]
    }


def test_qualifier_walks_listing_nodes():
    listing = {
        "nodes": [
            {
                "directory_path": "/notes",
                "children": [{"directory_path": "/notes/a.md", "file_path": "notes/a.md"}],
            }
        ]
    }

    assert PathQualifier(prefix="research").listing(listing) == {
        "nodes": [
            {
                "directory_path": "/research/notes",
                "children": [
                    {"directory_path": "/research/notes/a.md", "file_path": "research/notes/a.md"}
                ],
            }
        ]
    }


def test_hints_keep_the_pinned_project_id_of_a_mount():
    """A Cloud mount routes by the id of the workspace that listed it; its bare name
    can name another workspace's project, so a hint must not fall back to the name."""
    active = ProjectItem(id=1, external_id="ext-docs", name="docs", path="/docs")
    mount = ProjectPathRoute(project="docs", path="x", stripped=True, project_id="pinned-id")
    local = ProjectPathRoute(project="docs", path="x", stripped=True)

    assert hint_project_ref("acme/docs", mount, active) == "acme/docs"
    assert hint_project_ref(None, mount, active) == "pinned-id"
    assert hint_project_ref(None, local, active) == "docs"
    assert hint_project_ref(None, ProjectPathRoute(None, "x", False), active) == "ext-docs"
