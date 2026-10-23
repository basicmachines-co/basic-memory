"""bm_project and bm_schema: one name per group, commands delegate (#1832)."""

from typing import Any

import pytest
from fastmcp.exceptions import ToolError

from basic_memory.mcp.server import lifespan, mcp, set_project_delete_visibility
from basic_memory.mcp.tools import bm_project, bm_schema, bm_write
from basic_memory.mcp.tools.projects import bm_project_with_delete


async def _listed_bm_project() -> Any:
    # The server's own listing: entering a Client would run the lifespan, which
    # re-applies the config flag this test sets directly.
    (tool,) = [tool for tool in await mcp.list_tools() if tool.name == "bm_project"]
    return tool


# --- Delete is opt-in ---


@pytest.mark.asyncio
async def test_delete_is_hidden_by_default():
    tool = await _listed_bm_project()

    assert tool.parameters["properties"]["command"]["enum"] == ["list", "create", "recent"]
    assert tool.annotations.destructive_hint is False


@pytest.mark.asyncio
async def test_enabling_delete_swaps_in_the_destructive_variant():
    set_project_delete_visibility(mcp, True)
    try:
        tool = await _listed_bm_project()
    finally:
        set_project_delete_visibility(mcp, False)

    assert "delete" in tool.parameters["properties"]["command"]["enum"]
    assert tool.annotations.destructive_hint is True


@pytest.mark.asyncio
async def test_config_flag_enables_delete_through_lifespan(config_manager):
    cfg = config_manager.load_config()
    assert cfg.enable_project_delete is False
    cfg.enable_project_delete = True
    config_manager.save_config(cfg)

    try:
        async with lifespan(mcp):
            tool = await _listed_bm_project()
    finally:
        set_project_delete_visibility(mcp, False)

    assert tool.annotations.destructive_hint is True


# --- Commands ---


@pytest.mark.asyncio
async def test_list(app, test_project):
    result = await bm_project(command="list", output_format="json")

    assert isinstance(result, dict)
    assert test_project.name in {project["name"] for project in result["projects"]}


@pytest.mark.asyncio
async def test_create_and_delete(app, tmp_path_factory):
    project_root = tmp_path_factory.mktemp("bm-project-home")

    created = await bm_project(command="create", name="Scratch", path=str(project_root))
    deleted = await bm_project_with_delete(command="delete", name="Scratch")

    assert isinstance(created, str) and created.startswith("✓")
    assert isinstance(deleted, str) and deleted.startswith("✓")


@pytest.mark.asyncio
async def test_recent_maps_items_and_since(client, test_project):
    await bm_write(
        identifier="notes/fresh.md",
        content="- [fact] something new",
        project=test_project.name,
    )

    notes = await bm_project(
        command="recent",
        items=["note"],
        since="1d",
        output_format="json",
        project=test_project.name,
    )
    observations = await bm_project(
        command="recent", items=["observation"], output_format="json", project=test_project.name
    )

    assert isinstance(notes, list) and isinstance(observations, list)
    assert any(row["title"] == "fresh" for row in notes)
    assert all(row["type"] == "observation" for row in observations)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"command": "create", "name": "x"}, "create needs `name` and `path`"),
        ({"command": "create", "path": "/tmp/x"}, "create needs `name` and `path`"),
    ],
)
async def test_project_argument_refusals(arguments, message):
    with pytest.raises(ToolError, match=f"^INVALID_ARGUMENTS: {message}"):
        await bm_project(**arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["create", "delete"])
async def test_constrained_server_refuses_project_changes(monkeypatch, command):
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "only-this")

    with pytest.raises(ToolError, match="^UNSUPPORTED: .*constrained to project 'only-this'"):
        await bm_project_with_delete(command=command, name="x", path="/tmp/x")


@pytest.mark.asyncio
async def test_delete_needs_a_name():
    with pytest.raises(ToolError, match="^INVALID_ARGUMENTS: delete needs `name`"):
        await bm_project_with_delete(command="delete")


# --- bm_schema ---


@pytest.mark.asyncio
async def test_schema_commands_delegate(client, test_project):
    for name in ("ada", "grace"):
        await bm_write(
            identifier=f"people/{name}.md",
            content="---\ntype: person\n---\n\n- [role] engineer\n- works_at [[Lab]]",
            project=test_project.name,
        )

    inferred = await bm_schema(
        command="infer", note_type="person", output_format="json", project=test_project.name
    )
    assert isinstance(inferred, dict) and inferred["note_type"] == "person"
    assert inferred["suggested_schema"]

    with pytest.raises(ToolError, match="^NOT_FOUND: No schema note describes type 'person'"):
        await bm_schema(command="diff", note_type="person", project=test_project.name)
    with pytest.raises(ToolError, match="^NOT_FOUND: No schema note describes type 'person'"):
        await bm_schema(command="validate", note_type="person", project=test_project.name)
    with pytest.raises(ToolError, match="^NOT_FOUND: This project has no schema notes"):
        await bm_schema(command="validate", project=test_project.name)
    with pytest.raises(ToolError, match="^NOT_FOUND: No notes of type 'robot'"):
        await bm_schema(command="validate", note_type="robot", project=test_project.name)


PERSON_SCHEMA = (
    "---\ntitle: Person\ntype: schema\nentity: person\nversion: 1\n"
    "schema:\n  role: string, what they do\nsettings:\n  validation: warn\n---\n\n# Person"
)


@pytest.mark.asyncio
async def test_schema_reports_once_a_schema_exists(client, test_project):
    await bm_write(identifier="schemas/person.md", content=PERSON_SCHEMA, project=test_project.name)
    await bm_write(
        identifier="people/ada.md",
        content="---\ntype: person\n---\n\n- [role] engineer",
        project=test_project.name,
    )

    report = await bm_schema(
        command="validate", note_type="person", output_format="json", project=test_project.name
    )
    text = await bm_schema(command="validate", project=test_project.name)
    note = await bm_schema(
        command="validate", identifier="people/ada.md", project=test_project.name
    )
    drift = await bm_schema(
        command="diff", note_type="person", output_format="json", project=test_project.name
    )
    drift_text = await bm_schema(command="diff", note_type="person", project=test_project.name)
    inferred_text = await bm_schema(command="infer", note_type="person", project=test_project.name)

    assert isinstance(report, dict) and report["total_notes"] == 1
    assert isinstance(text, str) and isinstance(note, str)
    assert isinstance(drift, dict) and drift["schema_found"] is True
    assert isinstance(drift_text, str) and isinstance(inferred_text, str)


@pytest.mark.asyncio
async def test_infer_with_no_notes_of_the_type(client, test_project):
    with pytest.raises(ToolError, match="^NOT_FOUND: No notes of type 'robot'"):
        await bm_schema(command="infer", note_type="robot", project=test_project.name)


@pytest.mark.asyncio
async def test_infer_below_the_threshold(client, test_project):
    for name, category in (("ada", "role"), ("grace", "hobby"), ("alan", "pet")):
        await bm_write(
            identifier=f"people/{name}.md",
            content=f"---\ntype: person\n---\n\n- [{category}] something",
            project=test_project.name,
        )

    with pytest.raises(ToolError, match="^NOT_FOUND: No field appears in 90%"):
        await bm_schema(
            command="infer", note_type="person", threshold=0.9, project=test_project.name
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"command": "infer"}, "infer needs `note_type`"),
        ({"command": "diff"}, "diff needs `note_type`"),
        ({"command": "diff", "identifier": "a.md"}, "diff works on a note type, not one note"),
    ],
)
async def test_schema_argument_refusals(client, test_project, arguments, message):
    with pytest.raises(ToolError, match=f"^INVALID_ARGUMENTS: {message}"):
        await bm_schema(project=test_project.name, **arguments)
