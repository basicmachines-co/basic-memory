"""Tests for text/json output mode behavior on MCP tools used by openclaw-basic-memory."""

from __future__ import annotations

from pathlib import Path

import pytest

from basic_memory.mcp.tools import (
    bm_project,
    bm_write,
)


@pytest.mark.asyncio
async def test_write_note_text_and_json_modes(app, test_project):
    text_result = await bm_write(
        project=test_project.name,
        title="Mode Write Note",
        directory="mode-tests",
        content="# Mode Write Note\n\ninitial",
        output_format="text",
    )
    assert isinstance(text_result, str)
    assert "note" in text_result.lower()

    json_result = await bm_write(
        project=test_project.name,
        title="Mode Write Note",
        directory="mode-tests",
        content="# Mode Write Note\n\nupdated",
        output_format="json",
        overwrite=True,
    )
    assert isinstance(json_result, dict)
    assert json_result["title"] == "Mode Write Note"
    assert json_result["action"] in ("created", "updated")
    assert json_result["permalink"]
    assert json_result["file_path"]
    assert "checksum" in json_result


@pytest.mark.asyncio
async def test_list_and_create_project_text_and_json_modes(app, test_project, tmp_path):
    list_text = await bm_project(command="list", output_format="text")
    assert isinstance(list_text, str)
    assert test_project.name in list_text

    list_json = await bm_project(command="list", output_format="json")
    assert isinstance(list_json, dict)
    assert "projects" in list_json
    assert any(project["name"] == test_project.name for project in list_json["projects"])

    project_name = "mode-create-project"
    project_path = str(tmp_path.parent / (tmp_path.name + "-projects") / "mode-create-project")

    create_text = await bm_project(
        command="create",
        name=project_name,
        path=project_path,
        output_format="text",
    )
    assert isinstance(create_text, str)
    assert "mode-create-project" in create_text
    # external_id should appear in the human-readable output too, so users see
    # the UUID without re-listing projects.
    assert "External ID:" in create_text

    create_json_again = await bm_project(
        command="create",
        name=project_name,
        path=project_path,
        output_format="json",
    )
    assert isinstance(create_json_again, dict)
    assert create_json_again["name"] == project_name
    # Normalize path separators for cross-platform compatibility.
    assert Path(create_json_again["path"]) == Path(project_path)
    assert create_json_again["created"] is False
    assert create_json_again["already_exists"] is True
    # external_id (UUID) must be present so callers can immediately use it as
    # project_id in subsequent tool calls without a project list round-trip.
    assert isinstance(create_json_again["external_id"], str)
    assert len(create_json_again["external_id"]) > 0

    # Verify create-new JSON path also returns external_id (not just already-exists).
    new_project_name = "mode-create-fresh"
    new_project_path = str(tmp_path.parent / (tmp_path.name + "-projects") / "mode-create-fresh")
    create_json_new = await bm_project(
        command="create",
        name=new_project_name,
        path=new_project_path,
        output_format="json",
    )
    assert isinstance(create_json_new, dict)
    assert create_json_new["created"] is True
    assert create_json_new["already_exists"] is False
    assert isinstance(create_json_new["external_id"], str)
    assert len(create_json_new["external_id"]) > 0

    default_project_name = "mode-default-project"
    default_project_path = str(
        tmp_path.parent / (tmp_path.name + "-projects") / "mode-default-project"
    )
    await bm_project(
        command="create",
        name=default_project_name,
        path=default_project_path,
        set_default=True,
        output_format="text",
    )

    default_text_again = await bm_project(
        command="create",
        name=default_project_name,
        path=default_project_path,
        output_format="text",
    )
    assert "Set as default project\\n" not in default_text_again
    assert "Set as default project\n" in default_text_again
