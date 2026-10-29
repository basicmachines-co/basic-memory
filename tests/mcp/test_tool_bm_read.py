"""bm_read: notes whole and sliced, metadata reads, non-note files, refusals (#1830).

These run the real ASGI stack through the shared `client` fixture.
"""

import io
import json
from typing import Any

import pytest
import pytest_asyncio
from fastmcp.exceptions import ToolError
from mcp.types import EmbeddedResource, ImageContent
from PIL import Image as PILImage

from basic_memory.index.local_project import LocalProjectIndexRunner
from basic_memory.mcp.tools import bm_read, write_note

LONG_NOTE = "\n".join(
    ["# Plan", "", "## Goals", "- [goal] ship 1.0", "", "## Decisions"]
    + [
        f"Decision paragraph {n} with enough words to count against a token budget."
        for n in range(60)
    ]
)


async def read_text(identifier: str, **arguments: Any) -> str:
    result = await bm_read(identifier, **arguments)
    assert isinstance(result, str)
    return result


async def read_json(identifier: str, **arguments: Any) -> dict[str, Any]:
    result = await bm_read(identifier, output_format="json", **arguments)
    assert isinstance(result, dict)
    return result


async def _write(project_name: str, title: str, content: str, directory: str = "notes") -> None:
    await write_note(title=title, directory=directory, content=content, project=project_name)


@pytest_asyncio.fixture
async def non_note_files(project_repository, session_maker, test_project, test_files):
    """A PNG, a PDF, a GIF and a JSON file, indexed into the test project."""
    gif = io.BytesIO()
    PILImage.new("RGB", (4, 4), "red").save(gif, format="GIF")
    (test_files["image"].parent / "anim.gif").write_bytes(gif.getvalue())
    palette = io.BytesIO()
    PILImage.new("RGB", (4, 4), "blue").convert("P").save(palette, format="PNG")
    (test_files["image"].parent / "palette.png").write_bytes(palette.getvalue())
    (test_files["image"].parent / "data.json").write_text('{"k": 1}')
    runner = LocalProjectIndexRunner(
        project_repository=project_repository, session_maker=session_maker
    )
    await runner.index_project(test_project.id, force_full=True)
    return {
        "png": test_files["image"].name,
        "pdf": test_files["pdf"].name,
        "gif": "anim.gif",
        "json": "data.json",
        "palette": "palette.png",
    }


# --- Whole notes ---


@pytest.mark.asyncio
async def test_whole_note_text_includes_frontmatter_by_default(client, test_project):
    await _write(test_project.name, "Plan", "# Plan\n\nship it")

    text = await read_text("notes/Plan.md", project=test_project.name)

    assert text.startswith("---")
    assert "title: Plan" in text
    assert text.rstrip().endswith("ship it")


@pytest.mark.asyncio
async def test_whole_note_without_frontmatter(client, test_project):
    await _write(test_project.name, "Plan", "# Plan\n\nship it")

    text = await read_text("notes/Plan.md", project=test_project.name, include_frontmatter=False)

    # The blank line that followed the frontmatter stays, as read_note returned it.
    assert text.lstrip("\n").startswith("# Plan")
    assert "title: Plan" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", ["Plan", "notes/plan", "memory://notes/plan"])
async def test_title_permalink_and_memory_url_resolve(client, test_project, identifier):
    await _write(test_project.name, "Plan", "# Plan\n\nship it")

    payload = await read_json(identifier, project=test_project.name)

    assert payload["file_path"] == "notes/Plan.md"
    assert payload["frontmatter"]["title"] == "Plan"
    assert payload["checksum"]


# --- Slices ---


@pytest.mark.asyncio
async def test_section_read_is_numbered_with_coordinates(client, test_project):
    await _write(test_project.name, "Plan", LONG_NOTE)

    text = await read_text("notes/Plan.md", project=test_project.name, section="Goals")

    assert text.startswith("Lines ")
    assert "- [goal] ship 1.0" in text
    assert "Decision paragraph" not in text


@pytest.mark.asyncio
async def test_line_range_json_carries_continuation(client, test_project):
    await _write(test_project.name, "Plan", LONG_NOTE)

    payload = await read_json("notes/Plan.md", project=test_project.name, start_line=1, end_line=3)

    assert (payload["start_line"], payload["end_line"]) == (1, 3)
    assert payload["content"].splitlines()[0] == "---"
    assert payload["has_more"] is True
    assert (payload["next_start_line"], payload["next_end_line"]) == (4, 6)


@pytest.mark.asyncio
async def test_section_json_at_the_end_has_no_continuation(client, test_project):
    await _write(test_project.name, "Plan", LONG_NOTE)

    payload = await read_json("notes/Plan.md", project=test_project.name, section="Decisions")

    assert payload["has_more"] is False
    assert payload["next_start_line"] is None


@pytest.mark.asyncio
async def test_token_budget_says_where_to_continue(client, test_project):
    await _write(test_project.name, "Plan", LONG_NOTE)

    text = await read_text("notes/Plan.md", project=test_project.name, max_tokens=40)

    assert ", cut at the token budget\n" in text.splitlines(keepends=True)[0]
    assert "(Continue with start_line=" in text
    assert "Decision paragraph 59" not in text


@pytest.mark.asyncio
async def test_first_lines_via_end_line(client, test_project):
    # There is no `head` parameter; end_line alone reads from the top.
    await _write(test_project.name, "Plan", LONG_NOTE)

    text = await read_text("notes/Plan.md", project=test_project.name, end_line=2)

    assert text.startswith("Lines 1-2 of ")


# --- Metadata only ---


@pytest.mark.asyncio
async def test_metadata_read_returns_the_full_checksum(client, test_project):
    await _write(test_project.name, "Plan", "# Plan\n\nship it")

    payload = await read_json("notes/Plan.md", project=test_project.name, include_content=False)
    text = await read_text("notes/Plan.md", project=test_project.name, include_content=False)

    assert set(payload) == {"title", "permalink", "file_path", "checksum", "updated_at"}
    assert text.startswith("# Note metadata")
    assert f"checksum: {payload['checksum']}" in text


# --- Files that are not notes ---


@pytest.mark.asyncio
async def test_png_comes_back_as_image_content(client, test_project, non_note_files):
    result = await bm_read(non_note_files["png"], project=test_project.name)

    assert isinstance(result, ImageContent)
    assert result.mime_type == "image/jpeg"


@pytest.mark.asyncio
async def test_pdf_comes_back_as_an_embedded_document(client, test_project, non_note_files):
    result = await bm_read(non_note_files["pdf"], project=test_project.name)

    assert isinstance(result, EmbeddedResource)
    assert result.resource.mime_type == "application/pdf"


@pytest.mark.asyncio
async def test_json_file_comes_back_as_text(client, test_project, non_note_files):
    text = await read_text(non_note_files["json"], project=test_project.name)
    payload = await read_json(non_note_files["json"], project=test_project.name)

    assert json.loads(text) == {"k": 1}
    assert payload["content_type"] == "application/json"
    assert payload["file_path"] == "data.json"


@pytest.mark.asyncio
async def test_gif_is_refused_as_unsupported(client, test_project, non_note_files):
    with pytest.raises(ToolError, match="^UNSUPPORTED:"):
        await bm_read(non_note_files["gif"], project=test_project.name)


@pytest.mark.asyncio
async def test_slicing_a_non_note_file_is_refused(client, test_project, non_note_files):
    with pytest.raises(ToolError, match="^UNSUPPORTED:.*not a Markdown note"):
        await bm_read(non_note_files["json"], project=test_project.name, end_line=1)


# --- Refusals ---


@pytest.mark.asyncio
async def test_missing_note_is_not_found(client, test_project):
    with pytest.raises(ToolError, match="^NOT_FOUND:"):
        await bm_read("notes/Nope.md", project=test_project.name)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"section": "Goals", "start_line": 1}, "section cannot be combined"),
        ({"start_line": 5, "end_line": 2}, "end_line .2. is before start_line .5."),
        ({"include_content": False, "section": "Goals"}, "include_content=false cannot"),
    ],
)
async def test_invalid_argument_combinations(client, test_project, arguments, message):
    await _write(test_project.name, "Plan", LONG_NOTE)

    with pytest.raises(ToolError, match=f"^INVALID_ARGUMENTS: .*{message}"):
        await bm_read("notes/Plan.md", project=test_project.name, **arguments)


@pytest.mark.asyncio
async def test_metadata_text_carries_the_qualified_path(client, test_project):
    await _write(test_project.name, "Plan", "# Plan")
    prefix = test_project.permalink

    text = await read_text(
        f"{prefix}/notes/Plan.md", include_content=False, project=test_project.name
    )

    assert f"file_path: {prefix}/notes/Plan.md" in text


@pytest.mark.asyncio
async def test_a_palette_png_is_shown_as_an_image(client, test_project, non_note_files):
    """Palette mode has to become RGB before JPEG encoding."""
    result = await bm_read(non_note_files["palette"], project=test_project.name)

    assert isinstance(result, ImageContent)
