"""bm_search: ranked search, folder and note scopes, matching lines, refusals (#1830).

These run the real ASGI stack through the shared `client` fixture. Most tests pin
mode="text" so ranking does not depend on the embedding model.
"""

from typing import Any

import pytest
from fastmcp.exceptions import ToolError

from basic_memory.index.local_project import LocalProjectIndexRunner
from basic_memory.mcp.tools import bm_read, bm_search, write_note
from basic_memory.mcp.tools import ranked_search as ranked_search_module
from basic_memory.mcp.tools.ranked_search import _query_terms
from basic_memory.mcp.tools.search import MultiProjectFailed, MultiProjectNotIndexed


async def search_text(**arguments: Any) -> str:
    result = await bm_search(**arguments)
    assert isinstance(result, str)
    return result


async def search_json(**arguments: Any) -> dict[str, Any]:
    result = await bm_search(output_format="json", **arguments)
    assert isinstance(result, dict)
    return result


async def _seed(project_name: str) -> None:
    await write_note(
        title="Deploy Plan",
        directory="tasks",
        content=(
            "# Deploy Plan\n\nWe deploy on Fridays.\n\nRollback is manual.\n\n"
            "## Observations\n- [decision] deploy with blue-green #ops"
        ),
        metadata={"status": "active"},
        project=project_name,
    )
    await write_note(
        title="Deploy History",
        directory="notes",
        content="# Deploy History\n\nThe last deploy was quiet.",
        metadata={"status": "done"},
        project=project_name,
    )


# --- Ranked search ---


@pytest.mark.asyncio
async def test_text_search_lists_ranked_hits(client, test_project):
    await _seed(test_project.name)

    payload = await search_json(query="deploy", mode="text", project=test_project.name)
    text = await search_text(query="deploy", mode="text", project=test_project.name)

    assert {row["title"] for row in payload["results"]} == {"Deploy Plan", "Deploy History"}
    assert text.startswith(f"Results for 'deploy' in project '{test_project.name}':")
    assert "- tasks/Deploy Plan.md | Deploy Plan | score " in text


@pytest.mark.asyncio
async def test_default_mode_finds_the_note(client, test_project):
    await _seed(test_project.name)

    payload = await search_json(query="rollback", project=test_project.name)

    assert "Deploy Plan" in {row["title"] for row in payload["results"]}


@pytest.mark.asyncio
async def test_path_limits_the_search_to_a_folder(client, test_project):
    await _seed(test_project.name)

    payload = await search_json(
        query="deploy", mode="text", path="tasks", project=test_project.name
    )

    assert [row["title"] for row in payload["results"]] == ["Deploy Plan"]


@pytest.mark.asyncio
async def test_project_qualified_path_returns_qualified_paths(client, test_project):
    await _seed(test_project.name)

    payload = await search_json(query="deploy", mode="text", path=f"{test_project.permalink}/tasks")

    assert payload["results"][0]["file_path"] == f"{test_project.permalink}/tasks/Deploy Plan.md"


@pytest.mark.asyncio
async def test_filters_alone_search_frontmatter(client, test_project):
    await _seed(test_project.name)

    payload = await search_json(filters={"status": "done"}, project=test_project.name)

    assert [row["title"] for row in payload["results"]] == ["Deploy History"]


@pytest.mark.asyncio
async def test_items_and_categories_return_observations(client, test_project):
    await _seed(test_project.name)

    payload = await search_json(
        query="blue-green", mode="text", items=["observation"], project=test_project.name
    )

    assert payload["results"][0]["type"] == "observation"


@pytest.mark.asyncio
async def test_no_matches(client, test_project):
    await _seed(test_project.name)

    text = await search_text(query="zxqv", mode="text", project=test_project.name)

    assert text == f"No results for 'zxqv' in project '{test_project.name}'."


@pytest.mark.asyncio
async def test_project_relative_permalink_pattern(client, test_project):
    # #1812: permalinks carry the project prefix here, and 'tasks/*' still matches.
    await _seed(test_project.name)

    payload = await search_json(query="tasks/*", mode="permalink", project=test_project.name)

    assert [row["title"] for row in payload["results"]] == ["Deploy Plan"]


# --- Matching lines ---


@pytest.mark.asyncio
async def test_lines_return_matching_windows(client, test_project):
    await _seed(test_project.name)

    payload = await search_json(
        query="rollback", mode="text", lines=0, path="tasks", project=test_project.name
    )
    text = await search_text(
        query="rollback", mode="text", lines=0, path="tasks", project=test_project.name
    )

    (row,) = payload["results"]
    assert row["match_count"] == 1
    assert row["windows"][0]["content"] == "Rollback is manual."
    assert ": Rollback is manual." in text


@pytest.mark.asyncio
async def test_lines_match_any_query_term(client, test_project):
    await _seed(test_project.name)

    payload = await search_json(
        query="Fridays OR manual", mode="text", lines=0, path="tasks", project=test_project.name
    )

    (row,) = payload["results"]
    assert row["match_count"] == 2


# --- One note ---


@pytest.mark.asyncio
async def test_identifier_searches_one_note(client, test_project):
    await _seed(test_project.name)

    row = await search_json(
        query="deploy", identifier="tasks/Deploy Plan.md", lines=1, project=test_project.name
    )

    assert row["file_path"] == "tasks/Deploy Plan.md"
    assert row["match_count"] >= 2
    assert row["windows"]


@pytest.mark.asyncio
async def test_identifier_no_matches_is_explicit_and_keeps_json_identity(client, test_project):
    await _seed(test_project.name)

    text = await search_text(
        query="zebra", identifier="tasks/Deploy Plan.md", project=test_project.name
    )
    row = await search_json(
        query="zebra", identifier="tasks/Deploy Plan.md", project=test_project.name
    )

    assert "No matching lines" in text
    assert "tasks/Deploy Plan.md" in text
    assert "to continue" not in text
    assert row["file_path"] == "tasks/Deploy Plan.md"
    assert row["match_count"] == 0
    assert row["windows"] == []


@pytest.mark.asyncio
async def test_identifier_that_names_a_non_note_file(
    client, test_project, test_files, project_repository, session_maker
):
    runner = LocalProjectIndexRunner(
        project_repository=project_repository, session_maker=session_maker
    )
    await runner.index_project(test_project.id, force_full=True)

    with pytest.raises(ToolError, match="^UNSUPPORTED: .*not a Markdown note"):
        await bm_search(query="x", identifier=test_files["image"].name, project=test_project.name)


@pytest.mark.asyncio
async def test_identifier_that_names_no_note(client, test_project):
    with pytest.raises(ToolError, match="^NOT_FOUND:"):
        await bm_search(query="deploy", identifier="tasks/Nope.md", project=test_project.name)


# --- Query terms ---


@pytest.mark.parametrize(
    ("query", "terms"),
    [
        ("auth flow", ["auth", "flow"]),
        ('"auth flow" token', ["auth flow", "token"]),
        ("(coffee OR tea) NOT -decaf", ["coffee", "tea", "decaf"]),
        ("tag:ops deploy", ["deploy"]),
        ("tag:ops", ["tag:ops"]),
    ],
)
def test_query_terms(query, terms):
    assert _query_terms(query) == terms


# --- Refusals ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"query": "x", "identifier": "a.md", "path": "notes"}, "`identifier` and `path` each"),
        ({"query": "x", "path": "notes", "all_projects": True}, "`path` and `all_projects` each"),
        ({"query": "memory://a/b", "all_projects": True}, "memory:// query and `all_projects`"),
        ({"query": "memory://a/b", "projects": ["a"]}, "memory:// query and `projects`"),
        ({"query": "x", "lines": 1, "projects": ["a"]}, "not several"),
        ({"query": "x", "projects": []}, "`projects` is empty"),
        (
            {"query": "x", "all_projects": True, "page": 11, "page_size": 100},
            "at most result 1000; page 11 of 100 ends at 1100",
        ),
        ({"query": "x", "projects": ["a", "b"]}, "Give one"),
        ({"query": "x", "projects": ["a"], "all_projects": True}, "`projects` and `all_projects`"),
        ({"query": "x", "lines": 1, "compact": True}, "give one"),
        ({"lines": 1}, "need a `query`"),
        ({"query": "a\nb", "lines": 1}, "single-line query"),
        (
            {"query": "x", "valid_at": "2026-01-01", "valid_overlaps": "[2026-01-01,)"},
            "either valid_at",
        ),
        ({"query": "x", "valid_at": "not-a-date"}, "Invalid valid-time filter"),
        ({}, "Nothing to search for"),
        (
            {"query": "x", "identifier": "a.md", "filters": {"status": "active"}},
            "filters cannot apply",
        ),
        ({"query": "x", "identifier": "a.md", "mode": "text", "compact": True}, "mode, compact"),
        ({"query": "x", "identifier": "a.md", "page": 2}, "page cannot apply"),
    ],
)
async def test_invalid_arguments(client, test_project, arguments, message):
    with pytest.raises(ToolError, match=f"^INVALID_ARGUMENTS: .*{message}"):
        await bm_search(project=test_project.name, **arguments)


@pytest.mark.asyncio
async def test_text_results_continue_with_a_bm_search_call(client, test_project):
    await _seed(test_project.name)

    text = await search_text(query="deploy", mode="text", page_size=1, project=test_project.name)

    assert (
        f"Call bm_search(query='deploy', mode='text', project='{test_project.name}', "
        "page=2, page_size=1) to continue."
    ) in text


@pytest.mark.asyncio
async def test_identifier_text_shows_the_note_lines(client, test_project):
    await _seed(test_project.name)

    text = await search_text(
        query="rollback", identifier="tasks/Deploy Plan.md", project=test_project.name
    )

    assert ": Rollback is manual." in text
    assert "to continue" not in text


@pytest.mark.asyncio
async def test_lines_for_several_observations_of_one_note(client, test_project):
    """Observation hits share their note's id; the content batch reads it once."""
    await write_note(
        title="Runbook",
        directory="ops",
        content="# Runbook\n\n- [step] deploy the canary\n- [step] deploy the fleet",
        project=test_project.name,
    )

    payload = await search_json(
        query="deploy",
        mode="text",
        items=["observation"],
        lines=0,
        path="ops",
        project=test_project.name,
    )

    assert len(payload["results"]) == 2
    assert all(row["file_path"] == "ops/Runbook.md" for row in payload["results"])


@pytest.mark.asyncio
async def test_empty_search_across_projects_is_refused(client, test_project):
    """Searching several projects for nothing refuses, like one project does."""
    with pytest.raises(ToolError, match="^INVALID_ARGUMENTS: Nothing to search for"):
        await bm_search(all_projects=True)
    with pytest.raises(ToolError, match='"error": "INVALID_ARGUMENTS"'):
        await bm_search(projects=[test_project.name], output_format="json")


@pytest.mark.asyncio
async def test_a_search_that_ran_nowhere_is_an_error(client, test_project, monkeypatch):
    """Every database failing is UNAVAILABLE, not an empty successful search."""

    async def ran_nowhere(**_kwargs: Any) -> MultiProjectFailed:
        return MultiProjectFailed(reason="local: database is locked", rendered="# Search Failed")

    monkeypatch.setattr(ranked_search_module, "_search_all_projects", ran_nowhere)
    with pytest.raises(ToolError, match='"error": "UNAVAILABLE"'):
        await bm_search(query="deploy", all_projects=True, output_format="json")


@pytest.mark.asyncio
async def test_unindexed_projects_keep_the_json_shape(client, test_project, monkeypatch):
    async def not_indexed(**_kwargs: Any) -> MultiProjectNotIndexed:
        return MultiProjectNotIndexed(guidance="Index the project first.")

    monkeypatch.setattr(ranked_search_module, "_search_all_projects", not_indexed)
    payload = await search_json(query="deploy", all_projects=True)

    assert payload == {"results": [], "guidance": "Index the project first."}


@pytest.mark.asyncio
async def test_a_project_qualified_memory_url_routes_the_search(client, test_project):
    """'memory://<project>/...' searches the project it names, as a read would."""
    await _seed(test_project.name)

    payload = await search_json(query=f"memory://{test_project.permalink}/tasks/*")

    assert [row["title"] for row in payload["results"]] == ["Deploy Plan"]


@pytest.mark.asyncio
async def test_a_memory_url_with_a_folder_path_is_refused(client, test_project):
    """The URL names its own project; a folder could name another, so give one."""
    with pytest.raises(ToolError, match="^INVALID_ARGUMENTS: `path` and a memory:// query"):
        await bm_search(query=f"memory://{test_project.permalink}/tasks/*", path="tasks")


@pytest.mark.asyncio
async def test_results_across_projects_carry_routable_paths(client, test_project):
    """Each hit's path names its project, so bm_read can follow it in any session."""
    await _seed(test_project.name)

    payload = await search_json(query="rollback", mode="text", all_projects=True)
    text = await search_text(query="rollback", mode="text", all_projects=True)

    paths = [row["file_path"] for row in payload["results"]]
    assert f"{test_project.permalink}/tasks/Deploy Plan.md" in paths
    assert text.startswith("Results for 'rollback' in all projects:")
    note = await bm_read(paths[0])
    assert isinstance(note, str)


@pytest.mark.asyncio
async def test_max_matches_needs_a_line_scan(client, test_project):
    """Ranked results have no matching lines, so a max_matches bound cannot apply."""
    with pytest.raises(ToolError, match="^INVALID_ARGUMENTS: `max_matches` bounds"):
        await bm_search(query="deploy", max_matches=1, project=test_project.name)
