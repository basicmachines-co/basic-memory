"""bm_search: ranked search, folder and note scopes, matching lines, refusals (#1830).

These run the real ASGI stack through the shared `client` fixture. Most tests pin
mode="text" so ranking does not depend on the embedding model.
"""

from typing import Any

import pytest
from fastmcp.exceptions import ToolError

from basic_memory.index.local_project import LocalProjectIndexRunner
from basic_memory.mcp.tools import bm_search, write_note
from basic_memory.mcp.tools.ranked_search import _query_terms


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
        ({"query": "x", "identifier": "a.md", "path": "notes"}, "give one"),
        ({"query": "x", "path": "notes", "all_projects": True}, "search one project"),
        ({"lines": 1}, "need a `query`"),
        ({"query": "a\nb", "lines": 1}, "single-line query"),
        (
            {"query": "x", "valid_at": "2026-01-01", "valid_overlaps": "[2026-01-01,)"},
            "either valid_at",
        ),
        ({"query": "x", "valid_at": "not-a-date"}, "Invalid valid-time filter"),
        ({}, "Nothing to search for"),
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
