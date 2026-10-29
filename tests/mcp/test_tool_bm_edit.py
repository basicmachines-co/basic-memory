"""bm_edit: commands, shared section selector, no auto-create, refusals (#1831).

These run the real ASGI stack through the shared `client` fixture.
"""

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from basic_memory import db
from basic_memory.index.local_project import LocalProjectIndexRunner
from basic_memory.mcp.clients import KnowledgeClient
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools import bm_edit, bm_read, bm_write
from basic_memory.mcp.tools.edit import SECTION_COMMANDS
from basic_memory.workspace_context import workspace_permalink_context

NOTE = (
    "---\ntitle: Plan\nstatus: draft\n---\n\n# Plan\n\n## Goals\n\n- ship\n\n"
    "## Log\none\n\n## Log\ntwo\n"
)


async def _seed(project_name: str) -> str:
    result = await bm_write(
        identifier="notes/plan.md", content=NOTE, output_format="json", project=project_name
    )
    assert isinstance(result, dict)
    return result["checksum"]


async def edit_json(**arguments: Any) -> dict[str, Any]:
    result = await bm_edit(output_format="json", **arguments)
    assert isinstance(result, dict)
    return result


async def body(project_name: str) -> str:
    text = await bm_read("notes/plan.md", project=project_name)
    assert isinstance(text, str)
    return text


# --- Commands ---


@pytest.mark.asyncio
async def test_append_reports_the_new_checksum(client, test_project):
    checksum = await _seed(test_project.name)

    result = await edit_json(
        identifier="notes/plan.md", command="append", content="tail", project=test_project.name
    )

    assert result["action"] == "edited"
    assert result["command"] == "append"
    assert result["checksum"] != checksum
    assert (await body(test_project.name)).rstrip().endswith("tail")


@pytest.mark.asyncio
async def test_text_result_names_the_file_and_command(client, test_project):
    await _seed(test_project.name)

    text = await bm_edit(
        identifier="notes/plan.md", command="prepend", content="Intro", project=test_project.name
    )

    assert isinstance(text, str)
    assert text.startswith("Edited notes/plan.md (prepend)")


@pytest.mark.asyncio
async def test_find_replace(client, test_project):
    await _seed(test_project.name)

    await bm_edit(
        identifier="notes/plan.md",
        command="find_replace",
        find_text="- ship",
        content="- ship 1.0",
        project=test_project.name,
    )

    assert "- ship 1.0" in await body(test_project.name)


@pytest.mark.asyncio
async def test_replace_section_uses_the_read_selector(client, test_project):
    await _seed(test_project.name)

    await bm_edit(
        identifier="notes/plan.md",
        command="replace_section",
        section="Plan/Goals",
        content="- ship 1.0",
        project=test_project.name,
    )
    await bm_edit(
        identifier="notes/plan.md",
        command="replace_section",
        section="Log[1]",
        content="TWO",
        project=test_project.name,
    )

    text = await body(test_project.name)
    # The section keeps the blank lines around its body (#1815).
    assert "## Goals\n\n- ship 1.0\n\n## Log\none" in text
    assert text.rstrip().endswith("## Log\nTWO")


@pytest.mark.asyncio
async def test_repeated_heading_needs_an_index(client, test_project):
    await _seed(test_project.name)

    with pytest.raises(ToolError, match=r"^INVALID_ARGUMENTS: Multiple sections.*Log\[1\]"):
        await bm_edit(
            identifier="notes/plan.md",
            command="replace_section",
            section="Log",
            content="x",
            project=test_project.name,
        )


@pytest.mark.asyncio
async def test_set_metadata_leaves_the_body_alone(client, test_project):
    await _seed(test_project.name)
    before = await bm_read("notes/plan.md", include_frontmatter=False, project=test_project.name)

    await bm_edit(
        identifier="notes/plan.md",
        command="set_metadata",
        metadata={"status": "active"},
        project=test_project.name,
    )

    after = await bm_read("notes/plan.md", include_frontmatter=False, project=test_project.name)
    assert after == before
    assert "status: active" in await body(test_project.name)


# --- Guards ---


@pytest.mark.asyncio
async def test_expected_checksum_guards_the_edit(client, test_project):
    checksum = await _seed(test_project.name)

    with pytest.raises(ToolError, match=f"^REVISION_CONFLICT: .*{checksum}"):
        await bm_edit(
            identifier="notes/plan.md",
            command="append",
            content="x",
            expected_checksum="stale",
            project=test_project.name,
        )
    result = await edit_json(
        identifier="notes/plan.md",
        command="append",
        content="x",
        expected_checksum=checksum,
        project=test_project.name,
    )
    assert result["checksum"] != checksum


@pytest.mark.asyncio
async def test_missing_note_is_not_created(client, test_project):
    with pytest.raises(ToolError, match="^NOT_FOUND: .*\nTry instead: bm_write"):
        await bm_edit(
            identifier="notes/nope.md", command="append", content="x", project=test_project.name
        )


@pytest.mark.asyncio
async def test_api_refusal_keeps_the_server_message(client, test_project):
    await _seed(test_project.name)

    with pytest.raises(ToolError, match="^INVALID_ARGUMENTS: Text to replace not found"):
        await bm_edit(
            identifier="notes/plan.md",
            command="find_replace",
            find_text="absent",
            content="x",
            project=test_project.name,
        )


@pytest.mark.asyncio
async def test_old_parameter_spellings_are_accepted(client, test_project):
    """C12: Claude Code's Edit spellings and edit_note's `operation` reach bm_edit."""
    await _seed(test_project.name)
    async with Client(mcp) as mcp_client:
        result = await mcp_client.call_tool(
            "bm_edit",
            {
                "file_path": "notes/plan.md",
                "operation": "find_replace",
                "old_string": "- ship",
                "new_string": "- launch",
                "project": test_project.name,
            },
        )
    assert not result.is_error
    assert "- launch" in await body(test_project.name)


@pytest.mark.asyncio
async def test_no_answer_is_an_unknown_outcome(client, test_project, monkeypatch):
    await _seed(test_project.name)

    async def lost(*_args: Any, **_kwargs: Any) -> None:
        error = ToolError("timed out")
        error.__cause__ = httpx.ReadTimeout("timed out")
        raise error

    monkeypatch.setattr(KnowledgeClient, "patch_entity", lost)
    with pytest.raises(ToolError, match="^OUTCOME_UNKNOWN: .*\nTry instead: bm_read"):
        await bm_edit(
            identifier="notes/plan.md", command="append", content="x", project=test_project.name
        )


@pytest.mark.asyncio
async def test_note_deleted_after_resolving(client, test_project, monkeypatch):
    await _seed(test_project.name)

    async def deleted(*_args: Any, **_kwargs: Any) -> None:
        request = httpx.Request("PATCH", "http://test")
        answer = httpx.Response(
            409, request=request, json={"detail": {"message": "gone", "db_checksum": None}}
        )
        error = ToolError("conflict")
        error.__cause__ = httpx.HTTPStatusError("409", request=request, response=answer)
        raise error

    monkeypatch.setattr(KnowledgeClient, "patch_entity", deleted)
    with pytest.raises(ToolError, match="^REVISION_CONFLICT: .*was deleted"):
        await bm_edit(
            identifier="notes/plan.md",
            command="append",
            content="x",
            expected_checksum="abc",
            project=test_project.name,
        )


@pytest.mark.asyncio
async def test_non_note_files_are_refused(
    client, test_project, test_files, project_repository, session_maker
):
    runner = LocalProjectIndexRunner(
        project_repository=project_repository, session_maker=session_maker
    )
    await runner.index_project(test_project.id, force_full=True)

    with pytest.raises(ToolError, match="^UNSUPPORTED: .*not a Markdown note"):
        await bm_edit(
            identifier=test_files["image"].name,
            command="append",
            content="x",
            project=test_project.name,
        )


# --- Argument refusals ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"command": "append"}, "append needs `content`"),
        ({"command": "set_metadata"}, "set_metadata needs `metadata`"),
        ({"command": "set_metadata", "metadata": {"a": 1}, "content": "x"}, "frontmatter only"),
        ({"command": "find_replace", "content": "x"}, "needs `find_text`"),
        ({"command": "append", "content": "x", "find_text": "y"}, "only for find_replace"),
        ({"command": "replace_section", "content": "x"}, "needs `section`"),
        ({"command": "append", "content": "x", "section": "A"}, "only for section commands"),
        ({"command": "append", "content": "x", "metadata": {"a": None}}, "cannot be null"),
        ({"command": "set_metadata", "metadata": {"title": "New"}}, "cannot change title"),
        (
            {"command": "set_metadata", "metadata": {"permalink": "p", "status": "x"}},
            "cannot change permalink",
        ),
    ],
)
async def test_invalid_arguments(client, test_project, arguments, message):
    with pytest.raises(ToolError, match=f"^INVALID_ARGUMENTS: .*{message}"):
        await bm_edit(identifier="notes/plan.md", project=test_project.name, **arguments)


@pytest.mark.asyncio
async def test_refusal_guidance_keeps_the_project_prefix(client, test_project):
    """A suggested bm_read must reach the same project the edit addressed."""
    await _seed(test_project.name)
    prefix = test_project.permalink

    with pytest.raises(ToolError, match=f"bm_read\\(identifier='{prefix}/notes/plan.md', project="):
        await bm_edit(
            identifier=f"{prefix}/notes/plan.md",
            command="append",
            content="x",
            expected_checksum="stale",
            project=test_project.name,
        )


@pytest.mark.asyncio
async def test_guarded_edit_of_a_note_already_gone(client, test_project):
    """A pinned revision of a note that no longer exists is a conflict, not a miss."""
    with pytest.raises(ToolError, match="^REVISION_CONFLICT: 'notes/gone.md' was deleted"):
        await bm_edit(
            identifier="notes/gone.md",
            command="append",
            content="x",
            expected_checksum="abc",
            project=test_project.name,
        )


# --- Sections (ported from edit_note) ---

SPEC = (
    "# Spec\n\n## Implementation\nOld details.\n\n### Internals\nOld internals.\n\n"
    "## Testing\nTest info here."
)


async def _seed_spec(project_name: str) -> None:
    await bm_write(identifier="specs/spec.md", content=SPEC, project=project_name)


async def spec_text(project_name: str) -> str:
    text = await bm_read("specs/spec.md", include_frontmatter=False, project=project_name)
    assert isinstance(text, str)
    return text


@pytest.mark.asyncio
async def test_replace_section_takes_its_subsections_by_default(client, test_project):
    """The h3 under the h2 goes with it, through the next h2 (#1012)."""
    await _seed_spec(test_project.name)

    await bm_edit(
        identifier="specs/spec.md",
        command="replace_section",
        section="Implementation",
        content="New details.\n\n## Rollout\nShip behind a flag.\n",
        project=test_project.name,
    )

    text = await spec_text(test_project.name)
    assert "New details." in text and "## Rollout" in text
    assert "### Internals" not in text and "Old internals." not in text
    assert "Test info here." in text


@pytest.mark.asyncio
async def test_replace_section_can_keep_its_subsections(client, test_project):
    await _seed_spec(test_project.name)

    await bm_edit(
        identifier="specs/spec.md",
        command="replace_section",
        section="Implementation",
        content="New details.\n",
        replace_subsections=False,
        project=test_project.name,
    )

    text = await spec_text(test_project.name)
    assert "New details." in text and "Old details." not in text
    assert "### Internals\nOld internals." in text
    assert "Test info here." in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("insert_before_section", "INSERTED\n\n## Testing"),
        ("insert_after_section", "## Testing\nINSERTED"),
    ],
)
async def test_insert_around_a_section(client, test_project, command, expected):
    await _seed_spec(test_project.name)

    await bm_edit(
        identifier="specs/spec.md",
        command=command,
        section="Testing",
        content="INSERTED",
        project=test_project.name,
    )

    assert expected in await spec_text(test_project.name)


@pytest.mark.asyncio
@pytest.mark.parametrize("command", sorted(SECTION_COMMANDS))
async def test_a_missing_section_changes_nothing(client, test_project, command):
    await _seed_spec(test_project.name)
    before = await spec_text(test_project.name)

    with pytest.raises(ToolError, match="^INVALID_ARGUMENTS: .*Nonexistent"):
        await bm_edit(
            identifier="specs/spec.md",
            command=command,
            section="Nonexistent",
            content="x",
            project=test_project.name,
        )

    assert await spec_text(test_project.name) == before


# --- find_replace counts ---


@pytest.mark.asyncio
async def test_find_replace_must_match_the_expected_count(client, test_project):
    await bm_write(
        identifier="config/config.md",
        content="# Configuration\nVersion: v0.12.0\nSettings for v0.12.0 release.",
        project=test_project.name,
    )

    with pytest.raises(ToolError, match="^INVALID_ARGUMENTS: .*Expected 1 .*found 2"):
        await bm_edit(
            identifier="config/config.md",
            command="find_replace",
            find_text="v0.12.0",
            content="v0.13.0",
            project=test_project.name,
        )
    await bm_edit(
        identifier="config/config.md",
        command="find_replace",
        find_text="v0.12.0",
        content="v0.13.0",
        expected_replacements=2,
        project=test_project.name,
    )

    text = await bm_read("config/config.md", project=test_project.name)
    assert isinstance(text, str)
    assert "v0.12.0" not in text and text.count("v0.13.0") == 2


@pytest.mark.asyncio
async def test_whitespace_find_text_is_refused(client, test_project):
    await _seed(test_project.name)

    with pytest.raises(ToolError, match="^INVALID_ARGUMENTS:"):
        await bm_edit(
            identifier="notes/plan.md",
            command="find_replace",
            find_text="   ",
            content="x",
            project=test_project.name,
        )


# --- Identifiers ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identifier",
    ["notes/plan", "Plan", "notes/Plan", "notes/plan.md", "memory://notes/plan"],
)
async def test_identifier_spellings_edit_the_same_note(client, test_project, identifier):
    await _seed(test_project.name)

    result = await edit_json(
        identifier=identifier, command="append", content="tail", project=test_project.name
    )

    assert result["file_path"] == "notes/plan.md"


@pytest.mark.asyncio
async def test_memory_url_with_the_project_prefix(client, test_project):
    await _seed(test_project.name)

    result = await edit_json(
        identifier=f"memory://{test_project.name}/notes/plan",
        command="append",
        content="tail",
        project=test_project.name,
    )

    assert result["action"] == "edited"
    assert (await body(test_project.name)).rstrip().endswith("tail")


@pytest.mark.asyncio
async def test_a_near_miss_edits_nothing(client, test_project):
    """A missing note never falls through to a similar title."""
    for title in ("Routing Test A", "Routing Test B"):
        await bm_write(
            title=title, directory="test", content=f"# {title}\nContent.", project=test_project.name
        )

    with pytest.raises(ToolError, match="^NOT_FOUND:"):
        await bm_edit(
            identifier="Routing Test NONEXISTENT",
            command="find_replace",
            find_text="Content",
            content="replaced",
            project=test_project.name,
        )

    for title in ("Routing Test A", "Routing Test B"):
        assert "Content." in str(await bm_read(title, project=test_project.name))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identifier",
    ["../escape-note", "/tmp/outside-note", "notes/../../../escape", "~/escape-note"],
)
async def test_identifiers_outside_the_project_write_nothing(client, test_project, identifier):
    project_root = Path(test_project.path)

    with pytest.raises(ToolError, match="^(NOT_FOUND|OUTSIDE_PROJECT|INVALID_ARGUMENTS):"):
        await bm_edit(
            identifier=f"{test_project.permalink}/{identifier}",
            command="append",
            content="should never be written",
            project=test_project.name,
        )

    assert not (project_root.parent / "escape-note.md").exists()
    assert not (project_root.parent / "escape.md").exists()
    assert not Path("/tmp/outside-note.md").exists()


@pytest.mark.asyncio
async def test_team_workspace_memory_url_keeps_the_complete_permalink(client, test_project):
    expected_permalink = f"team-paul/{test_project.name}/team/tool-edit-note"
    with workspace_permalink_context(workspace_slug="team-paul", workspace_type="organization"):
        await bm_write(
            title="Tool Edit Note",
            directory="team",
            content="# Tool Edit Note\nOriginal content.",
            project=test_project.name,
        )
        result = await edit_json(
            identifier=f"memory://{expected_permalink}",
            command="append",
            content="\nAppended in team workspace.",
            project=test_project.name,
        )

    assert result["permalink"] == expected_permalink


# --- What an edit leaves behind ---


@pytest.mark.asyncio
async def test_repeated_edits_keep_the_permalink(client, test_project):
    """A note without a permalink in its frontmatter keeps the one it was indexed with (#170)."""
    await bm_write(
        title="Plain", directory="test", content="# Plain\nOriginal.", project=test_project.name
    )

    for line in ("First edit.", "Second edit."):
        result = await edit_json(
            identifier="test/plain", command="append", content=line, project=test_project.name
        )
        assert result["permalink"] == f"{test_project.name}/test/plain"


@pytest.mark.asyncio
async def test_empty_append_is_accepted(client, test_project):
    await _seed(test_project.name)

    result = await edit_json(
        identifier="notes/plan.md", command="append", content="", project=test_project.name
    )

    assert result["action"] == "edited"


@pytest.mark.asyncio
async def test_explicit_nulls_for_unused_fields_are_accepted(client, test_project):
    """MCP clients may send null for every optional field an append does not use."""
    await _seed(test_project.name)
    async with Client(mcp) as mcp_client:
        result = await mcp_client.call_tool(
            "bm_edit",
            {
                "identifier": "notes/plan.md",
                "command": "append",
                "content": "tail",
                "find_text": None,
                "section": None,
                "metadata": None,
                "expected_checksum": None,
                "project": test_project.name,
            },
        )

    assert not result.is_error
    assert (await body(test_project.name)).rstrip().endswith("tail")


@pytest.mark.asyncio
async def test_appended_observations_and_relations_are_indexed(
    client, test_project, entity_repository, session_maker
):
    await bm_write(
        identifier="features/spec.md",
        content="# Feature Spec\n\n- [design] Initial design #architecture\n- implements [[Base]]",
        project=test_project.name,
    )

    await bm_edit(
        identifier="features/spec.md",
        command="append",
        content="\n- [implementation] Added a feature #development\n- relates_to [[User Guide]]",
        project=test_project.name,
    )

    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, "features/spec.md")
    assert entity is not None
    assert {o.category for o in entity.observations} == {"design", "implementation"}
    assert {r.relation_type for r in entity.outgoing_relations} == {"implements", "relates_to"}
