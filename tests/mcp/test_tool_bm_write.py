"""bm_write: path and title addressing, folder case, overwrite guards, refusals (#1831).

These run the real ASGI stack through the shared `client` fixture.
"""

from typing import Any

import pytest
from fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import bm_read, bm_write


async def write_json(**arguments: Any) -> dict[str, Any]:
    result = await bm_write(output_format="json", **arguments)
    assert isinstance(result, dict)
    return result


async def write_text(**arguments: Any) -> str:
    result = await bm_write(**arguments)
    assert isinstance(result, str)
    return result


# --- Addressing ---


@pytest.mark.asyncio
async def test_path_write_lands_exactly_there(client, test_project):
    result = await write_json(
        identifier="plans/q4-plan.md", content="# Q4\n\nShip it.", project=test_project.name
    )

    assert result["action"] == "created"
    assert result["file_path"] == "plans/q4-plan.md"
    # No frontmatter title and no `title`: the file name is the title.
    assert result["title"] == "q4-plan"
    assert "requested_file_path" not in result


@pytest.mark.asyncio
async def test_path_write_adds_the_markdown_suffix(client, test_project):
    result = await write_json(identifier="plans/q4", content="Body", project=test_project.name)

    assert result["file_path"] == "plans/q4.md"


@pytest.mark.asyncio
async def test_frontmatter_title_is_kept_over_the_file_name(client, test_project):
    content = "---\ntitle: Quarterly Plan\n---\n\nBody"

    result = await write_json(
        identifier="plans/q4.md", title="Ignored", content=content, project=test_project.name
    )

    assert result["file_path"] == "plans/q4.md"
    assert result["title"] == "Quarterly Plan"


@pytest.mark.asyncio
async def test_title_names_a_path_note_without_frontmatter_title(client, test_project):
    result = await write_json(
        identifier="plans/q4.md", title="Q4 Plan", content="Body", project=test_project.name
    )

    assert result["title"] == "Q4 Plan"
    assert result["file_path"] == "plans/q4.md"


@pytest.mark.asyncio
async def test_title_and_directory_derive_the_file_name(client, test_project):
    result = await write_json(
        title="Weekly Standup", directory="meetings", content="Notes", project=test_project.name
    )

    assert result["file_path"] == "meetings/Weekly Standup.md"


@pytest.mark.asyncio
async def test_project_qualified_path_returns_a_qualified_path(client, test_project):
    result = await write_json(identifier=f"{test_project.permalink}/plans/q4.md", content="Body")

    assert result["file_path"] == f"{test_project.permalink}/plans/q4.md"
    note = await bm_read(result["file_path"])
    assert isinstance(note, str) and "Body" in note


# --- Folder case (#1796) ---


@pytest.mark.asyncio
async def test_write_into_existing_folder_spelling(client, test_project):
    await bm_write(identifier="Plans/seed.md", content="Seed", project=test_project.name)

    result = await write_json(identifier="plans/q4.md", content="Body", project=test_project.name)
    text = await write_text(identifier="plans/q5.md", content="Body", project=test_project.name)

    assert result["file_path"] == "Plans/q4.md"
    assert result["requested_file_path"] == "plans/q4.md"
    assert text.startswith("Created Plans/q5.md")
    assert "requested: plans/q5.md" in text


# --- Overwrite and revision guards ---


@pytest.mark.asyncio
async def test_existing_note_is_refused_without_overwrite(client, test_project):
    await bm_write(identifier="notes/a.md", content="One", project=test_project.name)

    with pytest.raises(ToolError, match="^ALREADY_EXISTS:.*\nTry instead: bm_edit"):
        await bm_write(
            identifier="notes/a.md", content="Two", overwrite=False, project=test_project.name
        )


@pytest.mark.asyncio
async def test_overwrite_replaces_and_reports_the_checksum(client, test_project):
    created = await write_json(identifier="notes/a.md", content="One", project=test_project.name)

    replaced = await write_json(
        identifier="notes/a.md", content="Two", overwrite=True, project=test_project.name
    )

    assert replaced["action"] == "updated"
    assert replaced["checksum"] != created["checksum"]


@pytest.mark.asyncio
async def test_expected_checksum_replaces_only_the_revision_read(client, test_project):
    created = await write_json(identifier="notes/a.md", content="One", project=test_project.name)

    with pytest.raises(ToolError, match=f"^REVISION_CONFLICT:.*{created['checksum']}"):
        await bm_write(
            identifier="notes/a.md",
            content="Two",
            expected_checksum="stale",
            project=test_project.name,
        )
    # expected_checksum implies overwrite.
    replaced = await write_json(
        identifier="notes/a.md",
        content="Two",
        expected_checksum=created["checksum"],
        project=test_project.name,
    )
    assert replaced["action"] == "updated"


@pytest.mark.asyncio
async def test_expected_checksum_for_a_missing_note(client, test_project):
    with pytest.raises(ToolError, match="^REVISION_CONFLICT: No note exists"):
        await bm_write(
            identifier="notes/new.md",
            content="Body",
            expected_checksum="abc",
            project=test_project.name,
        )


@pytest.mark.asyncio
async def test_refusals_carry_json_codes(client, test_project):
    await bm_write(identifier="notes/a.md", content="One", project=test_project.name)

    with pytest.raises(ToolError, match='"error": "ALREADY_EXISTS"'):
        await bm_write(
            identifier="notes/a.md",
            content="Two",
            overwrite=False,
            output_format="json",
            project=test_project.name,
        )


# --- Refusals ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "code", "message"),
    [
        ({"identifier": "a.md", "directory": "notes"}, "INVALID_ARGUMENTS", "not both"),
        ({"title": "A"}, "INVALID_ARGUMENTS", "Say where the note goes"),
        ({"directory": "notes"}, "INVALID_ARGUMENTS", "Say where the note goes"),
        (
            {"identifier": "a.md", "expected_checksum": "x", "overwrite": False},
            "INVALID_ARGUMENTS",
            "needs overwrite=true",
        ),
        ({"identifier": "data.json"}, "UNSUPPORTED", "not a Markdown file name"),
        ({"identifier": "../escape.md"}, "OUTSIDE_PROJECT", "outside the project"),
        ({"title": "A", "directory": "../up"}, "OUTSIDE_PROJECT", "outside the project"),
        ({"identifier": "notes/"}, "INVALID_ARGUMENTS", "not a note file"),
    ],
)
async def test_invalid_arguments(client, test_project, arguments, code, message):
    with pytest.raises(ToolError, match=f"^{code}: .*{message}"):
        await bm_write(content="Body", project=test_project.name, **arguments)
