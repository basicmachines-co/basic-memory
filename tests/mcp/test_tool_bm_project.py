"""bm_project and bm_schema: one name per group, commands delegate (#1832)."""

import json
from typing import Any

import pytest
from fastmcp.exceptions import ToolError

from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.clients import ProjectClient
from basic_memory.mcp.server import lifespan, mcp, set_project_delete_visibility
from basic_memory.mcp.tools import bm_project, bm_project_delete, bm_read, bm_schema, bm_write
from basic_memory.mcp.tools import projects as projects_module


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
    await bm_project(command="create", name="Scratch Two", path=str(project_root))
    deleted_json = await bm_project_delete(name="Scratch Two", output_format="json")

    assert isinstance(created, str) and created.startswith("✓")
    assert isinstance(deleted, str) and deleted.startswith("✓")
    assert isinstance(deleted_json, dict)
    assert deleted_json["action"] == "deleted" and deleted_json["name"] == "Scratch Two"


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

    assert isinstance(notes, dict) and isinstance(observations, dict)
    assert any(row["title"] == "fresh" for row in notes["results"])
    assert all(row["type"] == "observation" for row in observations["results"])


@pytest.mark.asyncio
async def test_recent_json_carries_paging(client, test_project):
    """A full page says another follows, so JSON callers know to ask for page 2."""
    for name in ("one", "two"):
        await bm_write(identifier=f"notes/{name}.md", content="# x", project=test_project.name)

    first = await bm_project(
        command="recent", page_size=1, output_format="json", project=test_project.name
    )

    assert isinstance(first, dict)
    assert (first["page"], first["page_size"], first["has_more"]) == (1, 1, True)
    assert len(first["results"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["text", "json"])
async def test_recent_requires_project(output_format):
    with pytest.raises(ToolError, match="recent needs `project`"):
        await bm_project(command="recent", output_format=output_format)


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
    """The refusal comes first, before a workspace selector is resolved or routed."""
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "only-this")

    with pytest.raises(ToolError, match="^UNSUPPORTED: Project creation disabled"):
        await bm_project(command="create", name="x", path="/tmp/x")
    with pytest.raises(ToolError, match="^UNSUPPORTED: Project creation disabled.*'only-this'"):
        await bm_project(command="create", name="x", path="/tmp/x", workspace="missing-team")
    with pytest.raises(ToolError, match="^UNSUPPORTED: Project deletion disabled"):
        await bm_project_delete(name="x")
    with pytest.raises(ToolError, match="^UNSUPPORTED: Project deletion disabled.*'only-this'"):
        await bm_project_delete(name="x", workspace="missing-team")
    with pytest.raises(ToolError, match='"error": "UNSUPPORTED"'):
        await bm_project_delete(name="x", output_format="json")


@pytest.mark.asyncio
async def test_create_api_refusals_honor_the_output_format(app, test_project):
    """The API refuses a project nested inside another; JSON keeps the refusal shape."""
    nested = f"{test_project.path}/nested"

    with pytest.raises(ToolError, match='"error": "INVALID_ARGUMENTS"'):
        await bm_project(command="create", name="Nested", path=nested, output_format="json")


# --- Local projects ---


@pytest.mark.asyncio
async def test_list_text_names_local_projects(app, test_project):
    result = await bm_project(command="list")

    assert isinstance(result, str)
    assert result.startswith("Available projects:")
    assert f"- {test_project.name} (local)" in result
    assert "cloud)" not in result


@pytest.mark.asyncio
async def test_constrained_server_lists_only_its_project(
    monkeypatch, app, test_project, tmp_path_factory
):
    await bm_project(command="create", name="other", path=str(tmp_path_factory.mktemp("other")))
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", test_project.name)

    text = await bm_project(command="list")
    payload = await bm_project(command="list", output_format="json")

    assert text == (
        f"Project: {test_project.name}\n\n"
        "Note: This MCP server is constrained to a single project.\n"
        "All operations will automatically use this project."
    )
    assert isinstance(payload, dict)
    assert [project["name"] for project in payload["projects"]] == [test_project.name]
    assert payload["constrained_project"] == test_project.name


@pytest.mark.asyncio
async def test_create_can_set_the_default_project(app, test_project, tmp_path_factory):
    project_root = tmp_path_factory.mktemp("new-default-project-home")

    created = await bm_project(
        command="create", name="New Default", path=str(project_root), set_default=True
    )
    listed = await bm_project(command="list", output_format="json")

    assert isinstance(created, str)
    assert "• Set as default project\n" in created
    assert isinstance(listed, dict)
    assert listed["default_project"] == "New Default"


@pytest.mark.asyncio
async def test_delete_keeps_note_files_by_default(app, tmp_path_factory):
    project_root = tmp_path_factory.mktemp("kept-project-home")
    await bm_project(command="create", name="Kept", path=str(project_root))

    deleted = await bm_project_delete(name="Kept")

    assert isinstance(deleted, str)
    assert "• Name: Kept" in deleted
    assert "Note files remain on disk" in deleted
    assert "Re-add the project" in deleted
    assert project_root.exists()


@pytest.mark.asyncio
async def test_delete_notes_removes_local_files(app, tmp_path_factory):
    """delete_notes=true reaches the API, which removes the directory before answering (#1034)."""
    project_root = tmp_path_factory.mktemp("delete-notes-project-home")
    (project_root / "note.md").write_text("# Note\n\ncontent\n")
    await bm_project(command="create", name="Delete Notes Project", path=str(project_root))

    deleted = await bm_project_delete(name="Delete Notes Project", delete_notes=True)

    assert isinstance(deleted, str)
    assert "Note files on disk were deleted along with the project" in deleted
    assert "Re-add the project" not in deleted
    assert not project_root.exists()


@pytest.mark.asyncio
async def test_create_repeated_for_the_same_directory_is_idempotent(app, tmp_path_factory):
    """The same directory, with a trailing slash and another name case, is not a new project."""
    project_root = tmp_path_factory.mktemp("same-path-project-home")
    created = await bm_project(
        command="create", name="Same Path", path=str(project_root), output_format="json"
    )
    retry = await bm_project(
        command="create", name="same path", path=f"{project_root}/", output_format="json"
    )
    retry_text = await bm_project(command="create", name="Same Path", path=str(project_root))

    assert isinstance(created, dict) and isinstance(retry, dict)
    assert (created["created"], created["already_exists"]) == (True, False)
    assert created["indexing"]["state"] == "completed"
    assert (retry["created"], retry["already_exists"]) == (False, True)
    assert (retry["name"], retry["path"]) == ("Same Path", created["path"])
    assert isinstance(retry_text, str)
    assert retry_text.startswith("✓ Project already exists: Same Path")
    assert "Project is already available for use in tool calls" in retry_text


@pytest.mark.asyncio
async def test_create_retry_indexes_a_project_that_was_never_indexed(app, tmp_path_factory):
    """A create whose indexing never ran is repaired by repeating the create."""
    project_root = tmp_path_factory.mktemp("partial-create-project-home")
    (project_root / "kept.md").write_text("# Kept\n\n- [fact] retained before the retry\n")
    # The project row exists but nothing indexed it: what a create leaves behind when
    # its indexing request fails.
    async with get_client() as client:
        await ProjectClient(client).create_project(
            {"name": "Partial", "path": str(project_root), "set_default": False}
        )

    retry = await bm_project(
        command="create", name="Partial", path=str(project_root), output_format="json"
    )
    note = await bm_read(identifier="kept.md", project="Partial")

    assert isinstance(retry, dict)
    assert retry["already_exists"] is True
    assert retry["indexing"]["state"] == "completed"
    assert "retained before the retry" in str(note)


@pytest.mark.asyncio
async def test_create_refuses_the_same_name_in_another_directory(app, tmp_path_factory):
    """A repeated name with another directory is refused, in either format (#1811)."""
    existing_root = tmp_path_factory.mktemp("mismatch-existing-home")
    requested_root = tmp_path_factory.mktemp("mismatch-requested-home")
    await bm_project(command="create", name="Mismatch", path=str(existing_root))

    with pytest.raises(ToolError) as refused:
        await bm_project(command="create", name="Mismatch", path=str(requested_root))
    with pytest.raises(ToolError) as refused_json:
        await bm_project(
            command="create", name="Mismatch", path=str(requested_root), output_format="json"
        )

    # Project paths are serialized with '/', while requested paths may use '\\' on Windows.
    assert str(refused.value).replace("\\", "/") == (
        f"ALREADY_EXISTS: Project 'Mismatch' already exists at "
        f"{existing_root.as_posix()}, not {requested_root.as_posix()}.\n"
        f"Try instead: another name, or bm_project(command='create', "
        f"name='Mismatch', path='{existing_root.as_posix()}')"
    )
    payload = json.loads(str(refused_json.value))
    assert payload["error"] == "ALREADY_EXISTS"
    assert payload["message"].replace("\\", "/") == (
        f"Project 'Mismatch' already exists at "
        f"{existing_root.as_posix()}, not {requested_root.as_posix()}."
    )


@pytest.mark.asyncio
async def test_workspace_without_cloud_credentials_fails_fast(app, test_project, tmp_path_factory):
    """A workspace selector names a Cloud tenant; without credentials nothing happens locally."""
    project_root = tmp_path_factory.mktemp("ws-local-project-home")

    with pytest.raises(RuntimeError, match="cloud workspace was requested \\('team-paul'\\)"):
        await bm_project(
            command="create", name="WS Local", path=str(project_root), workspace="team-paul"
        )
    with pytest.raises(RuntimeError, match="cloud workspace was requested \\('team-paul'\\)"):
        await bm_project_delete(name=test_project.name, workspace="team-paul")

    listed = await bm_project(command="list")
    assert isinstance(listed, str)
    assert "WS Local" not in listed and f"- {test_project.name}" in listed


@pytest.mark.asyncio
async def test_explicit_local_routing_keeps_a_workspace_create_local(
    monkeypatch, app, tmp_path_factory
):
    """--local wins over a workspace selector: the create runs and indexes locally."""
    monkeypatch.setenv("BASIC_MEMORY_EXPLICIT_ROUTING", "true")
    monkeypatch.setenv("BASIC_MEMORY_FORCE_LOCAL", "true")
    project_root = tmp_path_factory.mktemp("explicit-local-project-home")

    created = await bm_project(
        command="create",
        name="Explicit Local",
        path=str(project_root),
        workspace="some-workspace",
        output_format="json",
    )

    assert isinstance(created, dict)
    assert created["created"] is True
    assert created["indexing"]["state"] == "completed"


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
async def test_schema_api_refusals_honor_the_output_format(client, test_project):
    """An invalid inline schema is refused by the API; JSON keeps the refusal shape."""
    await bm_write(
        identifier="people/odd.md",
        content=(
            "---\ntype: invalid_mode\nschema:\n  name: string\nsettings:\n"
            "  validation: banana\n---\n\n- [name] Odd"
        ),
        project=test_project.name,
    )

    with pytest.raises(ToolError, match='"error": "INVALID_ARGUMENTS"'):
        await bm_schema(
            command="validate",
            identifier="people/odd.md",
            output_format="json",
            project=test_project.name,
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
    with pytest.raises(ToolError, match="^NOT_FOUND: # No Notes Found of Type 'robot'"):
        await bm_schema(command="infer", note_type="robot", project=test_project.name)


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
        ({"command": "validate", "note_type": "person", "identifier": "a.md"}, "Give `note_type`"),
        ({"command": "diff"}, "diff needs `note_type`"),
        ({"command": "diff", "identifier": "a.md"}, "diff works on a note type, not one note"),
    ],
)
async def test_schema_argument_refusals(client, test_project, arguments, message):
    with pytest.raises(ToolError, match=f"^INVALID_ARGUMENTS: {message}"):
        await bm_schema(project=test_project.name, **arguments)


@pytest.mark.asyncio
async def test_diff_without_notes_is_not_a_clean_diff(client, test_project):
    """A schema with no notes of its type compares nothing; that is not "no drift"."""
    await bm_write(identifier="schemas/person.md", content=PERSON_SCHEMA, project=test_project.name)

    with pytest.raises(ToolError, match="^NOT_FOUND: # No Notes Found of Type 'person'"):
        await bm_schema(command="diff", note_type="person", project=test_project.name)


@pytest.mark.asyncio
async def test_deleting_a_missing_project_is_not_found(app):
    with pytest.raises(ToolError, match='"error": "NOT_FOUND"'):
        await bm_project_delete(name="no-such-project", output_format="json")


@pytest.mark.asyncio
async def test_recent_guidance_keeps_the_json_shape(client, test_project, monkeypatch):
    async def not_indexed(**_kwargs: Any) -> str:
        return "Index the project first."

    monkeypatch.setattr(projects_module, "recent_activity_page", not_indexed)
    payload = await bm_project(command="recent", output_format="json", project=test_project.name)

    assert payload == {"results": [], "guidance": "Index the project first."}


@pytest.mark.asyncio
async def test_api_refusals_honor_the_output_format(app, test_project):
    """Deleting the default project is refused by the API; JSON keeps the refusal shape."""
    with pytest.raises(ToolError, match='"error": "INVALID_ARGUMENTS"'):
        await bm_project_delete(name=test_project.name, output_format="json")
