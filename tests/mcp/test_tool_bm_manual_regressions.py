"""Remaining bm_* manual regressions from #1843, through the local ASGI API."""

from typing import Any

import pytest
from fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import bm_context, bm_edit, bm_move, bm_read, bm_schema, bm_write


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["text", "json"])
async def test_note_changes_do_not_report_unindexed_graph_counts(
    client, test_project, output_format
):
    created = await bm_write(
        identifier="plans/launch.md",
        content="# Launch\n\n- [goal] ship the release\n\n- relates_to [[Other Plan]]",
        output_format=output_format,
        project=test_project.name,
    )
    edited = await bm_edit(
        identifier="plans/launch.md",
        command="append",
        content="\n- [decision] freeze the scope\n",
        output_format=output_format,
        project=test_project.name,
    )
    moved = await bm_move(
        identifier="plans/launch.md",
        destination="archive/",
        output_format=output_format,
        project=test_project.name,
    )

    for result in (created, edited, moved):
        if isinstance(result, dict):
            assert {"observations", "relations", "unresolved_relations"}.isdisjoint(result)
            assert result["checksum"]
        else:
            assert isinstance(result, str)
            assert "observations:" not in result
            assert "relations:" not in result
            assert "checksum:" in result
    note = await bm_read("archive/launch.md", project=test_project.name)
    assert isinstance(note, str)
    assert "[goal] ship the release" in note
    assert "[decision] freeze the scope" in note
    assert "relates_to [[Other Plan]]" in note


@pytest.mark.asyncio
@pytest.mark.parametrize("compact", [False, True])
async def test_context_glob_pages_only_notes_and_keeps_their_graph(client, test_project, compact):
    await bm_write(
        identifier="plans/other.md",
        content="# Other Plan\n\n- [goal] support the launch",
        project=test_project.name,
    )
    await bm_write(
        identifier="plans/launch.md",
        content="# Launch Plan\n\n- [goal] ship\n\n- relates_to [[other]]",
        project=test_project.name,
    )

    pages: list[dict[str, Any]] = []
    for page_number in (1, 2):
        result = await bm_context(
            identifier="plans/*",
            page=page_number,
            page_size=1,
            compact=compact,
            project=test_project.name,
        )
        assert isinstance(result, dict)
        pages.append(result)

    for page in pages:
        assert isinstance(page, dict)
        assert len(page["results"]) == 1
        assert page["results"][0]["primary_result"]["type"] == "entity"
    assert pages[0]["has_more"] is True
    assert pages[1]["has_more"] is False
    assert {page["results"][0]["primary_result"]["file_path"] for page in pages} == {
        "plans/other.md",
        "plans/launch.md",
    }
    if not compact:
        assert all(page["results"][0]["observations"] for page in pages)
        assert any(
            row["type"] == "relation"
            for page in pages
            for row in page["results"][0]["related_results"]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("max_tokens", [1, 20])
async def test_tiny_read_budget_returns_content_and_a_working_continuation(
    client, test_project, max_tokens
):
    first_line = "A long opening paragraph " * 30
    await bm_write(
        identifier="plans/long.md",
        content=f"\n\n{first_line}\n\nLast paragraph.",
        project=test_project.name,
    )

    first = await bm_read(
        "plans/long.md", max_tokens=max_tokens, output_format="json", project=test_project.name
    )
    text = await bm_read("plans/long.md", max_tokens=max_tokens, project=test_project.name)

    assert isinstance(first, dict)
    assert isinstance(text, str)
    assert first_line in first["content"]
    assert first_line in text
    assert first["has_more"] is True
    assert first["next_start_line"] > first["start_line"]
    following = await bm_read(
        "plans/long.md",
        start_line=first["next_start_line"],
        max_tokens=max_tokens,
        output_format="json",
        project=test_project.name,
    )
    assert isinstance(following, dict)
    assert "Last paragraph." in following["content"]
    assert following["has_more"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "note_type"),
    [("validate", None), ("validate", "plan"), ("diff", "plan"), ("infer", "missing")],
)
async def test_schema_guidance_names_the_public_bm_tools(client, test_project, command, note_type):
    await bm_write(
        identifier="plans/launch.md",
        content="---\ntype: plan\n---\n\n- [goal] ship",
        project=test_project.name,
    )

    with pytest.raises(ToolError) as refused:
        await bm_schema(command=command, note_type=note_type, project=test_project.name)

    guidance = str(refused.value)
    assert "bm_schema(command=" in guidance
    assert "schema_infer(" not in guidance
    assert "schema_validate" not in guidance
    assert "schema_diff" not in guidance
    assert "`write_note`" not in guidance
    assert "`search_notes`" not in guidance
