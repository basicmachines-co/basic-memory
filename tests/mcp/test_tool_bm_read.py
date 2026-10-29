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

from basic_memory import db
from basic_memory.index.local_project import LocalProjectIndexRunner
from basic_memory.mcp.tools import bm_read, bm_write
from basic_memory.workspace_context import workspace_permalink_context

LONG_NOTE = "\n".join(
    ["# Plan", "", "## Goals", "- [goal] ship 1.0", "", "## Decisions"]
    + [
        f"Decision paragraph {n} with enough words to count against a token budget."
        for n in range(60)
    ]
)


# A source text kept verbatim beside the notes, read a range at a time.
SOURCE_TEXT = "CHAPTER 1\nLoomings.\nCall me Ishmael.\nCHAPTER 2\nThe Carpet-Bag.\n"


async def read_text(identifier: str, **arguments: Any) -> str:
    result = await bm_read(identifier, **arguments)
    assert isinstance(result, str)
    return result


async def read_json(identifier: str, **arguments: Any) -> dict[str, Any]:
    result = await bm_read(identifier, output_format="json", **arguments)
    assert isinstance(result, dict)
    return result


async def _write(project_name: str, title: str, content: str, directory: str = "notes") -> None:
    await bm_write(title=title, directory=directory, content=content, project=project_name)


@pytest_asyncio.fixture
async def non_note_files(project_repository, session_maker, test_project, test_files):
    """A PNG, a PDF, a GIF, a JSON and a plain-text file, indexed into the test project."""
    gif = io.BytesIO()
    PILImage.new("RGB", (4, 4), "red").save(gif, format="GIF")
    (test_files["image"].parent / "anim.gif").write_bytes(gif.getvalue())
    palette = io.BytesIO()
    PILImage.new("RGB", (4, 4), "blue").convert("P").save(palette, format="PNG")
    (test_files["image"].parent / "palette.png").write_bytes(palette.getvalue())
    (test_files["image"].parent / "data.json").write_text('{"k": 1}')
    (test_files["image"].parent / "source.txt").write_text(SOURCE_TEXT)
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
        "txt": "source.txt",
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
async def test_a_text_file_reads_a_line_range(client, test_project, non_note_files):
    """A .txt source slices by line like a note: same coordinates, same continuation."""
    payload = await read_json(
        non_note_files["txt"], project=test_project.name, start_line=2, end_line=3
    )
    text = await read_text(
        non_note_files["txt"], project=test_project.name, start_line=2, end_line=3
    )

    assert payload["content"] == "Loomings.\nCall me Ishmael."
    assert (payload["start_line"], payload["end_line"], payload["total_lines"]) == (2, 3, 5)
    assert payload["has_more"] is True
    assert (payload["next_start_line"], payload["next_end_line"]) == (4, 5)
    assert payload["file_path"] == "source.txt"
    assert payload["content_type"].startswith("text/plain")
    assert text.startswith("Lines 2-3 of 5")
    assert "3: Call me Ishmael." in text


@pytest.mark.asyncio
async def test_a_text_file_reads_its_first_and_last_lines(client, test_project, non_note_files):
    head = await read_text(non_note_files["txt"], project=test_project.name, end_line=2)
    tail = await read_json(non_note_files["txt"], project=test_project.name, start_line=4)

    assert head.startswith("Lines 1-2 of 5")
    assert "1: CHAPTER 1" in head
    assert tail["content"] == "CHAPTER 2\nThe Carpet-Bag."
    assert tail["has_more"] is False
    assert tail["next_start_line"] is None


@pytest.mark.asyncio
async def test_a_text_file_range_past_the_end_is_refused(client, test_project, non_note_files):
    with pytest.raises(ToolError, match="start_line 9 is past the end of the document"):
        await bm_read(non_note_files["txt"], project=test_project.name, start_line=9)


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [{"section": "CHAPTER 1"}, {"max_tokens": 10}])
async def test_sections_and_budgets_stay_note_only(client, test_project, non_note_files, arguments):
    with pytest.raises(ToolError, match="^UNSUPPORTED:.*section and max_tokens apply only"):
        await bm_read(non_note_files["txt"], project=test_project.name, **arguments)


@pytest.mark.asyncio
async def test_a_line_range_on_a_binary_file_is_refused(client, test_project, non_note_files):
    with pytest.raises(ToolError, match="^UNSUPPORTED:.*not a text file"):
        await bm_read(non_note_files["pdf"], project=test_project.name, end_line=1)


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


# --- Identifier spellings (ported from read_note) ---


@pytest.mark.asyncio
async def test_unicode_content_round_trips(client, test_project):
    content = "# Test 🚀\nThis note has emoji 🎉 and unicode ♠♣♥♦"
    await _write(test_project.name, "Unicode Test", content, directory="test")

    text = await read_text("test/unicode-test", project=test_project.name)

    assert content in text


@pytest.mark.asyncio
@pytest.mark.parametrize("by_external_id", [False, True])
async def test_memory_url_with_the_project_prefix(client, test_project, by_external_id):
    """The prefix agrees with `project`, whether that names the project or its id."""
    await _write(test_project.name, "Prefixed", "prefixed body", directory="test")
    project = test_project.external_id if by_external_id else test_project.name

    payload = await read_json(f"memory://{test_project.name}/test/prefixed", project=project)

    assert payload["content"].rstrip().endswith("prefixed body")


@pytest.mark.asyncio
async def test_team_workspace_note_reads_by_its_complete_permalink(
    client, test_project, entity_repository, session_maker
):
    expected_permalink = f"team-paul/{test_project.name}/team/team-workspace-note"
    with workspace_permalink_context(workspace_slug="team-paul", workspace_type="organization"):
        written = await bm_write(
            title="Team Workspace Note",
            directory="team",
            content="Team canonical content",
            project=test_project.name,
        )
    assert f"permalink: {expected_permalink}" in written
    async with db.scoped_session(session_maker) as session:
        stored = await entity_repository.get_by_permalink(session, expected_permalink)
    assert stored is not None

    for identifier in (expected_permalink, f"memory://{expected_permalink}"):
        payload = await read_json(identifier, project=test_project.name)
        assert payload["permalink"] == expected_permalink
        assert payload["content"].rstrip().endswith("Team canonical content")


@pytest.mark.asyncio
async def test_team_workspace_permalink_is_complete_without_project_prefixes(
    client, test_project, config_manager
):
    config = config_manager.load_config()
    config.permalinks_include_project = False
    config_manager.save_config(config)
    expected_permalink = f"team-paul/{test_project.name}/team/team-no-project-prefix-note"

    with workspace_permalink_context(workspace_slug="team-paul", workspace_type="organization"):
        written = await bm_write(
            title="Team No Project Prefix Note",
            directory="team",
            content="Team content without project-prefix config",
            project=test_project.name,
        )
    assert f"permalink: {expected_permalink}" in written

    payload = await read_json(f"memory://{expected_permalink}", project=test_project.name)

    assert payload["permalink"] == expected_permalink


@pytest.mark.asyncio
async def test_personal_workspace_write_stores_the_complete_permalink(
    client, test_project, entity_repository, session_maker
):
    expected_permalink = f"personal/{test_project.name}/personal/personal-workspace-note"
    with workspace_permalink_context(workspace_slug="personal", workspace_type="personal"):
        written = await bm_write(
            title="Personal Workspace Note",
            directory="personal",
            content="Personal canonical content",
            project=test_project.name,
        )

    assert f"permalink: {expected_permalink}" in written
    async with db.scoped_session(session_maker) as session:
        stored = await entity_repository.get_by_permalink(session, expected_permalink)
    assert stored is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace_qualified", [False, True])
async def test_personal_workspace_still_reads_a_short_project_permalink(
    client, test_project, workspace_qualified
):
    """Notes written before workspace permalinks keep resolving, qualified or not."""
    short_permalink = f"{test_project.name}/personal/short-personal-note"
    await _write(
        test_project.name, "Short Personal Note", "Short personal content", directory="personal"
    )
    identifier = f"personal/{short_permalink}" if workspace_qualified else short_permalink

    with workspace_permalink_context(workspace_slug="personal", workspace_type="personal"):
        payload = await read_json(f"memory://{identifier}", project=test_project.name)

    assert payload["permalink"] == short_permalink
    assert payload["content"].rstrip().endswith("Short personal content")


# --- Identifiers that leave the project ---

TRAVERSAL_IDENTIFIERS = [
    "../secrets.txt",
    "../../etc/passwd",
    "notes/../../../etc/shadow",
    "..\\..\\Windows\\System32\\config\\SAM",
    "\\\\server\\share\\file.txt",
    "/etc/passwd",
    "C:\\Users\\user\\.env",
    "~/.ssh/id_rsa",
    "~\\AppData\\secrets",
    "memory://../../etc/passwd",
    "memory:///etc/passwd",
    "memory://notes/../../../etc/shadow",
    "notes/文档/../../../etc/passwd",
    "../" * 100 + "etc/malicious",
    " ../../../etc/passwd ",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", TRAVERSAL_IDENTIFIERS)
async def test_identifiers_outside_the_project_read_nothing(client, test_project, identifier):
    """The project-scoped API resolves identifiers, so none of these can reach a file."""
    await _write(test_project.name, "Inside", "inside body")

    with pytest.raises(ToolError, match="^(NOT_FOUND|OUTSIDE_PROJECT|INVALID_ARGUMENTS):"):
        await bm_read(identifier, project=test_project.name)


# --- Misses and line bounds ---


@pytest.mark.asyncio
async def test_missing_external_id_line_read_is_not_found(client, test_project):
    with pytest.raises(ToolError, match="^NOT_FOUND:"):
        await bm_read(
            "22222222-2222-4222-8222-222222222222",
            start_line=1,
            end_line=10,
            project=test_project.name,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["text", "json"])
async def test_line_read_past_the_end_reports_the_document_length(
    client, test_project, entity_repository, session_maker, output_format
):
    """A start_line past the last line is an error, not a 'Lines 500-N' read (#1634)."""
    await _write(test_project.name, "Short Lines", "one\ntwo")
    total = len(
        (await read_json("notes/Short Lines.md", project=test_project.name))["content"].splitlines()
    )
    async with db.scoped_session(session_maker) as session:
        stored = await entity_repository.get_by_file_path(session, "notes/Short Lines.md")
    assert stored is not None

    for identifier in ("notes/Short Lines.md", stored.external_id):
        with pytest.raises(
            ToolError, match=f"start_line 500 is past the end of the document \\({total} lines\\)"
        ):
            await bm_read(
                identifier,
                start_line=500,
                end_line=510,
                output_format=output_format,
                project=test_project.name,
            )


@pytest.mark.asyncio
async def test_read_checksum_guards_an_overwrite(client, test_project):
    """The checksum bm_read returns is the revision bm_write's expected_checksum checks."""
    written = await bm_write(
        identifier="notes/guarded.md",
        content="first revision",
        output_format="json",
        project=test_project.name,
    )
    read = await read_json("notes/guarded.md", project=test_project.name)
    assert isinstance(written, dict)
    assert read["checksum"] == written["checksum"]

    replaced = await bm_write(
        identifier="notes/guarded.md",
        content="second revision",
        overwrite=True,
        expected_checksum=read["checksum"],
        output_format="json",
        project=test_project.name,
    )
    assert isinstance(replaced, dict)
    assert replaced["action"] == "updated"

    with pytest.raises(ToolError, match="REVISION_CONFLICT"):
        await bm_write(
            identifier="notes/guarded.md",
            content="stale third revision",
            overwrite=True,
            expected_checksum=read["checksum"],
            project=test_project.name,
        )
