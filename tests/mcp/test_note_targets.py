"""Routing a bm_* target into its project and resolving notes exactly (#1829).

These run the real ASGI stack through the shared `client` fixture.
"""

import json
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from basic_memory.mcp.clients import KnowledgeClient
from basic_memory.mcp.note_targets import open_project_target, resolve_note
from basic_memory.mcp.tools import write_note


async def _write_plan(project_name: str) -> None:
    await write_note(
        title="Plan",
        directory="notes",
        content="# Plan\n\nship it",
        project=project_name,
    )


@pytest.mark.asyncio
async def test_resolves_a_note_by_path_in_the_named_project(client, test_project):
    await _write_plan(test_project.name)

    async with open_project_target("notes/Plan.md", test_project.name, None) as target:
        external_id = await resolve_note(target, "notes/Plan.md")

    assert target.project.name == test_project.name
    assert target.route.path == "notes/Plan.md"
    assert external_id


@pytest.mark.asyncio
async def test_project_external_id_routes_like_the_name(client, test_project):
    await _write_plan(test_project.name)

    async with open_project_target("Plan", test_project.external_id, None) as target:
        external_id = await resolve_note(target, "Plan")

    assert target.project.external_id == test_project.external_id
    assert external_id


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
    assert "Try instead: bm_search(query='notes/Nope.md')" in message


@pytest.mark.asyncio
async def test_a_missing_note_refusal_in_json(client, test_project):
    async with open_project_target("notes/Nope.md", test_project.name, None) as target:
        with pytest.raises(ToolError) as refused:
            await resolve_note(target, "notes/Nope.md", output_format="json")

    payload = json.loads(str(refused.value))
    assert payload["error"] == "NOT_FOUND"
    assert payload["try_instead"] == "bm_search(query='notes/Nope.md')"


@pytest.mark.asyncio
async def test_a_bare_project_name_is_not_a_note(client, test_project):
    identifier = f"{test_project.name}/"
    async with open_project_target(identifier, None, None) as target:
        with pytest.raises(ToolError) as refused:
            await resolve_note(target, identifier)

    message = str(refused.value)
    assert message.startswith("INVALID_ARGUMENTS:")
    assert f"bm_list(path='{test_project.name}/')" in message


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

    monkeypatch.setattr(KnowledgeClient, "resolve_entity", failing_resolve)

    async with open_project_target("notes/Plan.md", test_project.name, None) as target:
        with pytest.raises(ToolError, match="backend unavailable"):
            await resolve_note(target, "notes/Plan.md")
