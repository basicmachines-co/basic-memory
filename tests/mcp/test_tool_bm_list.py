"""bm_list: project discovery at '/', one-level folder listings, round-trip paths (#1830).

These run the real ASGI stack through the shared `client` fixture.
"""

from typing import Any

import pytest
from fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import bm_list, bm_read, write_note


async def list_text(**arguments: Any) -> str:
    result = await bm_list(**arguments)
    assert isinstance(result, str)
    return result


async def list_json(**arguments: Any) -> dict[str, Any]:
    result = await bm_list(output_format="json", **arguments)
    assert isinstance(result, dict)
    return result


async def _seed(project_name: str) -> None:
    for title in ("Alpha", "Beta", "Gamma"):
        await write_note(title=title, directory="notes", content=f"# {title}", project=project_name)
    await write_note(
        title="Deep", directory="notes/archive", content="# Deep", project=project_name
    )


# --- Project discovery ---


@pytest.mark.asyncio
async def test_root_without_a_project_lists_projects(client, test_project):
    text = await list_text(path="/")
    payload = await list_json(path="/")

    assert text.startswith("Projects:")
    assert test_project.name in text
    assert f"/{test_project.permalink}" in {node["directory_path"] for node in payload["nodes"]}


# --- Folder listings ---


@pytest.mark.asyncio
async def test_lists_one_level_with_subfolders_first(client, test_project):
    await _seed(test_project.name)

    payload = await list_json(path="notes", project=test_project.name)

    kinds = [node["type"] for node in payload["nodes"]]
    assert kinds[0] == "directory"
    names = {node["name"] for node in payload["nodes"]}
    assert {"archive", "Alpha.md", "Beta.md", "Gamma.md"} <= names
    # One level only: the archived note is not listed.
    assert "Deep.md" not in names


@pytest.mark.asyncio
async def test_glob_filters_files(client, test_project):
    await _seed(test_project.name)

    payload = await list_json(path="notes", glob="B*", project=test_project.name)

    files = [node["name"] for node in payload["nodes"] if node["type"] == "file"]
    assert files == ["Beta.md"]


@pytest.mark.asyncio
async def test_text_listing_continues_with_a_bm_list_call(client, test_project):
    await _seed(test_project.name)

    text = await list_text(path="notes", page_size=2, project=test_project.name)

    assert text.startswith("Contents of 'notes':")
    assert f"bm_list(path='notes', project='{test_project.name}', page=2, page_size=2)" in text


@pytest.mark.asyncio
async def test_empty_folder(client, test_project):
    text = await list_text(path="nothing-here", glob="*.md", project=test_project.name)

    assert text == "No files in 'nothing-here' matching '*.md'."


# --- Round-trip paths ---


@pytest.mark.asyncio
async def test_project_qualified_listing_returns_qualified_paths(client, test_project):
    await _seed(test_project.name)

    payload = await list_json(path=f"{test_project.permalink}/notes")

    file_paths = {node["file_path"] for node in payload["nodes"] if node["type"] == "file"}
    assert f"{test_project.permalink}/notes/Alpha.md" in file_paths


@pytest.mark.asyncio
async def test_bm_read_returns_the_path_it_was_given(client, test_project):
    await _seed(test_project.name)

    payload = await bm_read(f"{test_project.permalink}/notes/Alpha.md", output_format="json")

    assert isinstance(payload, dict)
    assert payload["file_path"] == f"{test_project.permalink}/notes/Alpha.md"


# --- Refusals ---


@pytest.mark.asyncio
async def test_listing_outside_the_project_is_refused(client, test_project):
    with pytest.raises(ToolError, match="^OUTSIDE_PROJECT:"):
        await bm_list(path="../elsewhere", project=test_project.name)
