"""Tests for text/json output mode behavior on MCP tools used by openclaw-basic-memory."""

from __future__ import annotations

import pytest

from basic_memory.mcp.tools import (
    build_context,
    create_memory_project,
    delete_note,
    edit_note,
    list_memory_projects,
    move_note,
    read_note,
    recent_activity,
    write_note,
)


@pytest.mark.asyncio
async def test_write_note_text_and_json_modes(app, test_project):
    text_result = await write_note.fn(
        project=test_project.name,
        title="Mode Write Note",
        directory="mode-tests",
        content="# Mode Write Note\n\ninitial",
        output_format="text",
    )
    assert isinstance(text_result, str)
    assert "note" in text_result.lower()

    json_result = await write_note.fn(
        project=test_project.name,
        title="Mode Write Note",
        directory="mode-tests",
        content="# Mode Write Note\n\nupdated",
        output_format="json",
    )
    assert isinstance(json_result, dict)
    assert json_result["title"] == "Mode Write Note"
    assert json_result["action"] in ("created", "updated")
    assert json_result["permalink"]
    assert json_result["file_path"]
    assert "checksum" in json_result


@pytest.mark.asyncio
async def test_read_note_text_and_json_modes(app, test_project):
    await write_note.fn(
        project=test_project.name,
        title="Mode Read Note",
        directory="mode-tests",
        content="# Mode Read Note\n\nbody",
    )

    text_result = await read_note.fn(
        identifier="mode-tests/mode-read-note",
        project=test_project.name,
        output_format="text",
    )
    assert isinstance(text_result, str)
    assert "Mode Read Note" in text_result

    json_result = await read_note.fn(
        identifier="mode-tests/mode-read-note",
        project=test_project.name,
        output_format="json",
    )
    assert isinstance(json_result, dict)
    assert json_result["title"] == "Mode Read Note"
    assert json_result["permalink"]
    assert json_result["file_path"]
    assert isinstance(json_result["content"], str)
    assert "frontmatter" in json_result

    missing_json = await read_note.fn(
        identifier="mode-tests/missing-note",
        project=test_project.name,
        output_format="json",
    )
    assert isinstance(missing_json, dict)
    assert set(["title", "permalink", "file_path", "content", "frontmatter"]).issubset(
        missing_json.keys()
    )


@pytest.mark.asyncio
async def test_edit_note_text_and_json_modes(app, test_project):
    await write_note.fn(
        project=test_project.name,
        title="Mode Edit Note",
        directory="mode-tests",
        content="# Mode Edit Note\n\nstart",
    )

    text_result = await edit_note.fn(
        identifier="mode-tests/mode-edit-note",
        operation="append",
        content="\n\ntext-append",
        project=test_project.name,
        output_format="text",
    )
    assert isinstance(text_result, str)
    assert "Edited note" in text_result

    json_result = await edit_note.fn(
        identifier="mode-tests/mode-edit-note",
        operation="append",
        content="\n\njson-append",
        project=test_project.name,
        output_format="json",
    )
    assert isinstance(json_result, dict)
    assert json_result["title"] == "Mode Edit Note"
    assert json_result["operation"] == "append"
    assert json_result["permalink"]
    assert json_result["file_path"]
    assert "checksum" in json_result


@pytest.mark.asyncio
async def test_recent_activity_text_and_json_modes(app, test_project):
    await write_note.fn(
        project=test_project.name,
        title="Mode Activity Note",
        directory="mode-tests",
        content="# Mode Activity Note\n\nactivity",
    )

    text_result = await recent_activity.fn(
        project=test_project.name,
        timeframe="7d",
        output_format="text",
    )
    assert isinstance(text_result, str)
    assert "Recent Activity" in text_result

    json_result = await recent_activity.fn(
        project=test_project.name,
        timeframe="7d",
        output_format="json",
    )
    assert isinstance(json_result, list)
    assert any(item.get("title") == "Mode Activity Note" for item in json_result)
    for item in json_result:
        assert set(["title", "permalink", "file_path", "created_at"]).issubset(item.keys())


@pytest.mark.asyncio
async def test_list_and_create_project_text_and_json_modes(app, test_project, tmp_path):
    list_text = await list_memory_projects.fn(output_format="text")
    assert isinstance(list_text, str)
    assert test_project.name in list_text

    list_json = await list_memory_projects.fn(output_format="json")
    assert isinstance(list_json, dict)
    assert "projects" in list_json
    assert any(project["name"] == test_project.name for project in list_json["projects"])

    project_name = "mode-create-project"
    project_path = str(tmp_path.parent / (tmp_path.name + "-projects") / "mode-create-project")

    create_text = await create_memory_project.fn(
        project_name=project_name,
        project_path=project_path,
        output_format="text",
    )
    assert isinstance(create_text, str)
    assert "mode-create-project" in create_text

    create_json_again = await create_memory_project.fn(
        project_name=project_name,
        project_path=project_path,
        output_format="json",
    )
    assert isinstance(create_json_again, dict)
    assert create_json_again["name"] == project_name
    assert create_json_again["path"] == project_path
    assert create_json_again["created"] is False
    assert create_json_again["already_exists"] is True


@pytest.mark.asyncio
async def test_delete_note_text_and_json_modes(app, test_project):
    await write_note.fn(
        project=test_project.name,
        title="Mode Delete Text",
        directory="mode-tests",
        content="# Mode Delete Text",
    )

    text_delete = await delete_note.fn(
        identifier="mode-tests/mode-delete-text",
        project=test_project.name,
        output_format="text",
    )
    assert text_delete is True

    await write_note.fn(
        project=test_project.name,
        title="Mode Delete Json",
        directory="mode-tests",
        content="# Mode Delete Json",
    )

    json_delete = await delete_note.fn(
        identifier="mode-tests/mode-delete-json",
        project=test_project.name,
        output_format="json",
    )
    assert isinstance(json_delete, dict)
    assert json_delete["deleted"] is True
    assert json_delete["title"] == "Mode Delete Json"
    assert json_delete["permalink"]
    assert json_delete["file_path"]


@pytest.mark.asyncio
async def test_move_note_text_and_json_modes(app, test_project):
    await write_note.fn(
        project=test_project.name,
        title="Mode Move Text",
        directory="mode-tests",
        content="# Mode Move Text",
    )

    text_move = await move_note.fn(
        identifier="mode-tests/mode-move-text",
        destination_path="mode-tests/moved/mode-move-text.md",
        project=test_project.name,
        output_format="text",
    )
    assert isinstance(text_move, str)
    assert "moved" in text_move.lower()

    await write_note.fn(
        project=test_project.name,
        title="Mode Move Json",
        directory="mode-tests",
        content="# Mode Move Json",
    )

    json_move = await move_note.fn(
        identifier="mode-tests/mode-move-json",
        destination_path="mode-tests/moved/mode-move-json.md",
        project=test_project.name,
        output_format="json",
    )
    assert isinstance(json_move, dict)
    assert json_move["moved"] is True
    assert json_move["title"] == "Mode Move Json"
    assert json_move["source"] == "mode-tests/mode-move-json"
    assert json_move["destination"] == "mode-tests/moved/mode-move-json.md"
    assert json_move["permalink"]
    assert json_move["file_path"]


@pytest.mark.asyncio
async def test_build_context_json_default_and_text_mode(client, test_graph, test_project):
    json_result = await build_context.fn(
        project=test_project.name,
        url="memory://test/root",
    )
    assert isinstance(json_result, dict)
    assert "results" in json_result

    text_result = await build_context.fn(
        project=test_project.name,
        url="memory://test/root",
        output_format="text",
    )
    assert isinstance(text_result, str)
    assert "# Context:" in text_result
