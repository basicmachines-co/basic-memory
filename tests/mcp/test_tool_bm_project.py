"""bm_project and bm_schema: one name per group, commands delegate (#1832)."""

import pytest
from fastmcp.exceptions import ToolError

from basic_memory.mcp.server import lifespan, mcp, set_project_delete_visibility
from basic_memory.mcp.tools import bm_project, bm_project_delete, bm_schema, bm_write


async def _listed_names() -> set[str]:
    # The server's own listing: entering a Client would run the lifespan, which
    # re-applies the config flag these tests set directly.
    return {tool.name for tool in await mcp.list_tools()}


# --- Delete is opt-in ---


@pytest.mark.asyncio
async def test_project_delete_is_hidden_by_default():
    names = await _listed_names()

    assert "bm_project" in names
    assert "bm_project_delete" not in names


@pytest.mark.asyncio
async def test_bm_project_is_never_destructive():
    (tool,) = [tool for tool in await mcp.list_tools() if tool.name == "bm_project"]

    assert tool.parameters["properties"]["command"]["enum"] == ["list", "create", "recent"]
    assert tool.annotations is not None
    assert tool.annotations.destructive_hint is False


@pytest.mark.asyncio
async def test_config_flag_lists_project_delete_through_lifespan(config_manager):
    cfg = config_manager.load_config()
    assert cfg.enable_project_delete is False
    cfg.enable_project_delete = True
    config_manager.save_config(cfg)

    try:
        async with lifespan(mcp):
            names = await _listed_names()
    finally:
        set_project_delete_visibility(mcp, False)

    assert "bm_project_delete" in names


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
    deleted = await bm_project_delete(name="Scratch")

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
async def test_constrained_server_refuses_project_changes(monkeypatch):
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "only-this")

    with pytest.raises(ToolError, match="^UNSUPPORTED: Project creation disabled"):
        await bm_project(command="create", name="x", path="/tmp/x")
    with pytest.raises(ToolError, match="^UNSUPPORTED: Project deletion disabled"):
        await bm_project_delete(name="x")


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

    # The replaced tools now refuse "nothing to report" instead of returning it (C11).
    with pytest.raises(ToolError, match="^NOT_FOUND: # No Schema Found for 'person'"):
        await bm_schema(command="diff", note_type="person", project=test_project.name)
    with pytest.raises(ToolError, match="^NOT_FOUND: # No Schema Found for 'person'"):
        await bm_schema(command="validate", note_type="person", project=test_project.name)
    with pytest.raises(ToolError, match="^NOT_FOUND: # No Schemas Defined"):
        await bm_schema(command="validate", project=test_project.name)
    with pytest.raises(ToolError, match='"error": "NOT_FOUND"'):
        await bm_schema(
            command="validate", note_type="robot", output_format="json", project=test_project.name
        )


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
async def test_infer_below_the_threshold(client, test_project):
    for name, category in (("ada", "role"), ("grace", "hobby"), ("alan", "pet")):
        await bm_write(
            identifier=f"people/{name}.md",
            content=f"---\ntype: person\n---\n\n- [{category}] something",
            project=test_project.name,
        )

    with pytest.raises(ToolError, match="^NOT_FOUND: # No Schema Pattern Found"):
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
