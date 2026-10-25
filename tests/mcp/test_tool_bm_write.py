"""bm_write: path and title addressing, folder case, overwrite guards, refusals (#1831).

These run the real ASGI stack through the shared `client` fixture.
"""

from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from basic_memory.config import ConfigManager
from basic_memory.mcp.clients import KnowledgeClient
from basic_memory.mcp.server import mcp
from basic_memory.mcp.similar_notes import SimilarNote
from basic_memory.mcp.tools import bm_read, bm_write
from basic_memory.mcp.tools import write as write_module
from basic_memory.schemas.v2.note_write import NoteLocked, NoteTargetMoved


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


@pytest.mark.asyncio
async def test_requested_path_is_qualified_like_the_request(client, test_project):
    await bm_write(identifier="Plans/seed.md", content="Seed", project=test_project.name)
    prefix = test_project.permalink

    result = await write_json(identifier=f"{prefix}/plans/q4.md", content="Body")

    assert result["file_path"] == f"{prefix}/Plans/q4.md"
    assert result["requested_file_path"] == f"{prefix}/plans/q4.md"


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
    with pytest.raises(ToolError, match="^REVISION_CONFLICT: .*was deleted since you read it"):
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


# --- Content details ---


@pytest.mark.asyncio
async def test_tags_and_metadata_land_in_frontmatter(client, test_project):
    await bm_write(
        identifier="notes/tagged.md",
        content="Body",
        tags="a,b",
        metadata={"status": "draft"},
        project=test_project.name,
    )

    text = await bm_read("notes/tagged.md", project=test_project.name)
    assert isinstance(text, str)
    assert "status: draft" in text and "- a" in text and "- b" in text


@pytest.mark.asyncio
async def test_root_path_and_blank_frontmatter_title(client, test_project):
    result = await write_json(
        identifier="top.md", content="---\ntitle: ''\n---\nBody", project=test_project.name
    )

    assert result["file_path"] == "top.md"
    assert result["title"] == "top"


@pytest.mark.asyncio
async def test_frontmatter_without_title_and_unresolved_relations(client, test_project):
    text = await write_text(
        identifier="notes/linked.md",
        content="---\nstatus: draft\n---\n\n- relates_to [[No Such Note]]",
        project=test_project.name,
    )

    assert "title: linked" in text
    assert "relations:" not in text
    note = await bm_read("notes/linked.md", project=test_project.name)
    assert isinstance(note, str) and "relates_to [[No Such Note]]" in note


@pytest.mark.asyncio
async def test_malformed_frontmatter_is_an_argument_error(client, test_project):
    with pytest.raises(
        ToolError, match="(?s)^INVALID_ARGUMENTS: Invalid YAML.*Nothing was written"
    ):
        await bm_write(
            identifier="notes/bad.md",
            content="---\ntitle: [unclosed\n---\nbody",
            project=test_project.name,
        )


@pytest.mark.asyncio
async def test_unanswered_write_is_an_unknown_outcome(client, test_project, monkeypatch):
    async def lost(*_args: Any, **_kwargs: Any) -> None:
        error = ToolError("timed out")
        error.__cause__ = httpx.ReadTimeout("timed out")
        raise error

    monkeypatch.setattr(KnowledgeClient, "write_note", lost)
    with pytest.raises(ToolError, match="^OUTCOME_UNKNOWN: .*\nTry instead: bm_read"):
        await bm_write(identifier="notes/a.md", content="Body", project=test_project.name)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "pattern"),
    [
        (
            NoteTargetMoved(
                external_id="abc", title="A", file_path="moved/a.md", permalink="moved/a"
            ),
            r"^ALREADY_EXISTS: .*moved to 'moved/a.md'.*\nTry instead: bm_read\(identifier='moved/a.md'\)",
        ),
        (NoteLocked(message="Note is locked for review."), "^LOCKED: Note is locked"),
    ],
)
async def test_typed_refusals(client, test_project, monkeypatch, outcome, pattern):
    async def answer(*_args: Any, **_kwargs: Any) -> Any:
        return outcome

    monkeypatch.setattr(KnowledgeClient, "write_note", answer)
    with pytest.raises(ToolError, match=pattern):
        await bm_write(
            identifier="notes/a.md", content="Body", overwrite=True, project=test_project.name
        )


# --- Similar-note advisory ---


@pytest.mark.asyncio
async def test_similar_notes_are_listed_with_bm_verbs(client, test_project, monkeypatch):
    async def neighbors(*_args: Any, **_kwargs: Any) -> list[SimilarNote]:
        return [SimilarNote(title="Old Plan", permalink="notes/old-plan", file_path="notes/o.md")]

    monkeypatch.setattr(write_module, "find_similar_notes", neighbors)
    text = await write_text(identifier="notes/new.md", content="Body", project=test_project.name)
    payload = await write_json(
        identifier="notes/newer.md", content="Body", project=test_project.name
    )

    assert "## Similar existing notes" in text
    assert "bm_edit(identifier='notes/o.md'" in text
    assert "bm_delete(identifier='notes/new.md')" in text
    assert payload["similar_notes"][0]["title"] == "Old Plan"


@pytest.mark.asyncio
async def test_similar_note_calls_reach_the_routed_project(client, test_project, monkeypatch):
    async def neighbors(*_args: Any, **_kwargs: Any) -> list[SimilarNote]:
        return [SimilarNote(title="Old Plan", permalink="notes/old-plan", file_path="notes/o.md")]

    monkeypatch.setattr(write_module, "find_similar_notes", neighbors)
    prefix = test_project.permalink
    text = await write_text(identifier=f"{prefix}/notes/new.md", content="Body")
    payload = await write_json(identifier=f"{prefix}/notes/newer.md", content="Body")

    assert f"bm_edit(identifier='{prefix}/notes/o.md'" in text
    assert f"bm_delete(identifier='{prefix}/notes/new.md')" in text
    assert payload["similar_notes"][0]["file_path"] == f"{prefix}/notes/o.md"


@pytest.mark.asyncio
async def test_a_failed_probe_does_not_fail_the_write(client, test_project, monkeypatch):
    async def declined(*_args: Any, **_kwargs: Any) -> list[SimilarNote]:
        raise ToolError("semantic search is disabled")

    monkeypatch.setattr(write_module, "find_similar_notes", declined)
    result = await write_json(identifier="notes/a.md", content="Body", project=test_project.name)

    assert result["action"] == "created"
    assert result["similar_notes"] == []


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
        ({"identifier": "C++/plan.md"}, "INVALID_ARGUMENTS", "would be written as 'C'"),
        ({"title": "A", "directory": "a@b"}, "INVALID_ARGUMENTS", "would be written as 'ab'"),
    ],
)
async def test_invalid_arguments(client, test_project, arguments, code, message):
    with pytest.raises(ToolError, match=f"^{code}: .*{message}"):
        await bm_write(content="Body", project=test_project.name, **arguments)


@pytest.mark.asyncio
async def test_markdown_suffix_is_kept(client, test_project):
    result = await write_json(
        identifier="notes/plan.markdown", content="Body", project=test_project.name
    )

    assert result["file_path"] == "notes/plan.markdown"


@pytest.mark.asyncio
async def test_existing_note_hint_names_the_qualified_path(client, test_project):
    prefix = test_project.permalink
    await bm_write(identifier=f"{prefix}/notes/a.md", content="One")

    with pytest.raises(ToolError, match=f"bm_edit\\(identifier='{prefix}/notes/a.md'"):
        await bm_write(identifier=f"{prefix}/notes/a.md", content="Two", overwrite=False)


@pytest.mark.asyncio
async def test_similarity_floor_is_local_only(client, test_project, monkeypatch):
    """The measured floor fits this process's model; a routed project uses its server's."""
    floors: list[float | None] = []

    async def capture(
        *_args: Any, min_similarity: float | None, **_kwargs: Any
    ) -> list[SimilarNote]:
        floors.append(min_similarity)
        return []

    monkeypatch.setattr(write_module, "find_similar_notes", capture)
    await bm_write(identifier="notes/local.md", content="Body", project=test_project.name)
    monkeypatch.setattr(write_module, "local_project_path", lambda *_args: None)
    await bm_write(identifier="notes/routed.md", content="Body", project=test_project.name)

    assert floors[1] is None
    assert floors[0] == write_module.similar_notes_min_similarity(ConfigManager().config)


@pytest.mark.asyncio
@pytest.mark.parametrize("note_type", ["", "x" * 201])
async def test_note_type_is_validated_as_an_argument(client, test_project, note_type):
    """An empty or overlong note_type is an argument error naming the parameter."""
    async with Client(mcp) as mcp_client:
        result = await mcp_client.call_tool(
            "bm_write",
            {
                "identifier": "notes/typed.md",
                "content": "# Typed",
                "note_type": note_type,
                "project": test_project.name,
            },
            raise_on_error=False,
        )
    assert result.is_error
    assert "note_type" in result.content[0].text  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(ToolError, match="^NOT_FOUND:"):
        await bm_read(identifier="notes/typed.md", project=test_project.name)
