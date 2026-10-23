"""bm_find: recursive listings by name, frontmatter queries, field projection (#1830).

These run the real ASGI stack through the shared `client` fixture.
"""

from typing import Any

import pytest
from fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import bm_find, write_note


async def find_text(**arguments: Any) -> str:
    result = await bm_find(**arguments)
    assert isinstance(result, str)
    return result


async def find_json(**arguments: Any) -> dict[str, Any]:
    result = await bm_find(output_format="json", **arguments)
    assert isinstance(result, dict)
    return result


async def _seed(project_name: str) -> None:
    await write_note(
        title="Alpha",
        directory="tasks",
        content="# Alpha",
        metadata={"status": "active", "priority": 2},
        project=project_name,
    )
    await write_note(
        title="Beta",
        directory="tasks/archive",
        content="# Beta",
        metadata={"status": "done", "priority": 1},
        project=project_name,
    )
    await write_note(
        title="Gamma",
        directory="notes",
        content="# Gamma",
        note_type="person",
        metadata={"status": "active"},
        project=project_name,
    )


# --- Listing arm ---


@pytest.mark.asyncio
async def test_name_glob_searches_recursively(client, test_project):
    await _seed(test_project.name)

    payload = await find_json(path="tasks", name="B*", project=test_project.name)

    files = {node["file_path"] for node in payload["nodes"] if node["type"] == "file"}
    assert "tasks/archive/Beta.md" in files


@pytest.mark.asyncio
async def test_depth_bounds_the_recursion(client, test_project):
    await _seed(test_project.name)

    payload = await find_json(path="tasks", name="B*", depth=1, project=test_project.name)

    files = {node["file_path"] for node in payload["nodes"] if node["type"] == "file"}
    assert "tasks/archive/Beta.md" not in files


@pytest.mark.asyncio
async def test_listing_text_continues_with_a_bm_find_call(client, test_project):
    await _seed(test_project.name)

    text = await find_text(path="/", page_size=1, project=test_project.name)

    assert text.startswith("Files under '/':")
    assert f"bm_find(path='/', page=2, page_size=1, project='{test_project.name}')" in text


@pytest.mark.asyncio
async def test_no_matches(client, test_project):
    text = await find_text(path="/", name="*.nothing", project=test_project.name)

    assert text == "No files under '/' matching '*.nothing'."


# --- Frontmatter arm ---


@pytest.mark.asyncio
async def test_filters_match_frontmatter_across_the_subtree(client, test_project):
    await _seed(test_project.name)

    payload = await find_json(filters={"status": "active"}, project=test_project.name)
    scoped = await find_json(path="tasks", filters={"status": "active"}, project=test_project.name)

    assert {row["title"] for row in payload["results"]} == {"Alpha", "Gamma"}
    assert {row["title"] for row in scoped["results"]} == {"Alpha"}


@pytest.mark.asyncio
async def test_operator_filters(client, test_project):
    await _seed(test_project.name)

    payload = await find_json(filters={"priority": {"$gte": 2}}, project=test_project.name)

    assert [row["title"] for row in payload["results"]] == ["Alpha"]


@pytest.mark.asyncio
async def test_note_type_key_reads_as_frontmatter_type(client, test_project):
    await _seed(test_project.name)

    payload = await find_json(filters={"note_type": "person"}, project=test_project.name)

    assert [row["title"] for row in payload["results"]] == ["Gamma"]


@pytest.mark.asyncio
async def test_fields_project_typed_frontmatter_values(client, test_project):
    await _seed(test_project.name)

    payload = await find_json(
        path="tasks", filters={"status": "active"}, fields=["priority"], project=test_project.name
    )
    text = await find_text(
        path="tasks", filters={"status": "active"}, fields=["priority"], project=test_project.name
    )

    (row,) = payload["results"]
    assert row["fields"] == {"priority": 2}
    assert "content" not in row
    assert "tasks/Alpha.md | Alpha | priority=2" in text


@pytest.mark.asyncio
async def test_frontmatter_query_with_no_matches(client, test_project):
    await _seed(test_project.name)

    text = await find_text(filters={"status": "blocked"}, project=test_project.name)

    assert text == "No notes under '/' match the filters."


# --- Refusals ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"filters": {}}, "`filters` is empty"),
        ({"filters": {"status": "active"}, "name": "*.md"}, "`name` cannot combine"),
        ({"filters": {"status": "active"}, "depth": 2}, "`depth` cannot combine"),
        ({"fields": ["priority"]}, "`fields` needs `filters`"),
        ({"filters": {"status": "active"}, "fields": ["review..approved"]}, "malformed field"),
    ],
)
async def test_invalid_argument_combinations(client, test_project, arguments, message):
    with pytest.raises(ToolError, match=f"^INVALID_ARGUMENTS: .*{message}"):
        await bm_find(project=test_project.name, **arguments)
