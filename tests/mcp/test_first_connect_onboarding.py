"""Tests for MCP first-connect onboarding (levers 1 & 3).

These cover the changes that help a freshly-connected model orient itself and offer a first
note against an empty knowledge base, without ever writing one unprompted:

- the server `instructions` string (surfaced at the `initialize` handshake),
- the enriched empty-state returns of `recent_activity` (the orientation call) and `search`,
- the `getting_started` prompt.
"""

import pytest

from basic_memory.mcp.prompts.getting_started import getting_started
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools import recent_activity, search_notes
from basic_memory.mcp.tools.search import _format_search_markdown
from basic_memory.schemas.search import SearchResponse


# --- Server instructions (lever 1a) ---


def test_server_sets_first_connect_instructions():
    """The server must ship instructions that orient the model and offer a first note."""
    instructions = mcp.instructions
    assert instructions is not None
    # Orients the model to what Basic Memory is and points at the first-note action.
    assert "Basic Memory" in instructions
    assert "recent_activity" in instructions
    assert "write_note" in instructions
    # Offer-not-act: the model must be told not to write unprompted.
    assert "unprompted" in instructions


# --- recent_activity empty-state (lever 1b, primary) ---


@pytest.mark.asyncio
async def test_recent_activity_empty_project_offers_first_note(client, test_project):
    """An empty project (no test_graph) should surface the offer-not-act first-note guidance."""
    result = await recent_activity(project=test_project.name, timeframe="7d")  # pyright: ignore[reportGeneralTypeIssues]

    assert isinstance(result, str)
    assert "No recent activity" in result
    assert "write_note" in result
    # The offer must be gated on the user agreeing first.
    assert "wait for them to agree" in result
    # The widened-window suggestion must stay scoped to the same project.
    assert f'recent_activity(project="{test_project.name}", timeframe="30d")' in result


@pytest.mark.asyncio
async def test_recent_activity_populated_project_has_no_first_note_offer(
    client, test_project, test_graph
):
    """A populated project must not nag with the first-note offer."""
    result = await recent_activity(project=test_project.name, timeframe="1w")  # pyright: ignore[reportGeneralTypeIssues]

    assert "wait for them to agree" not in result


# --- search empty-state (lever 1b, delegates to recent_activity) ---


@pytest.mark.asyncio
async def test_search_no_results_points_to_recent_activity(client, test_project):
    """Empty search must point at recent_activity rather than repeat the first-note offer."""
    result = await search_notes(query="XYZ123NoSuchNote", project=test_project.name)  # pyright: ignore[reportGeneralTypeIssues]

    assert isinstance(result, str)
    assert "No results found" in result
    # The orientation call must stay scoped to the searched project.
    assert f'recent_activity(project="{test_project.name}")' in result
    # It should NOT duplicate the write_note offer here (that would nag established users).
    assert "write_note" not in result


def test_search_empty_all_projects_suggests_discovery_recent_activity():
    """'all projects' is not a real project id, so the all-projects empty case must suggest a
    bare recent_activity() (discovery mode), not recent_activity(project="all projects")."""
    empty = SearchResponse(results=[], current_page=1, page_size=10)

    all_projects = _format_search_markdown(empty, "all projects", "q")
    assert "recent_activity()" in all_projects
    assert 'recent_activity(project="all projects")' not in all_projects

    scoped = _format_search_markdown(empty, "myproj", "q")
    assert 'recent_activity(project="myproj")' in scoped


# --- getting_started prompt (lever 3a) ---


@pytest.mark.asyncio
async def test_getting_started_prompt_is_registered():
    """The prompt must be registered so clients can surface it as a starter."""
    prompts = await mcp.list_prompts()
    names = {p.name for p in prompts}
    assert "getting_started" in names


@pytest.mark.asyncio
async def test_getting_started_offers_first_note_conditionally(client, test_project):
    """The guide offers a first note but conditions it on the user being new, and keeps the
    'already have notes' path — it must not assert emptiness (a quiet base looks the same)."""
    result = await getting_started(project=test_project.name)  # pyright: ignore[reportGeneralTypeIssues]

    assert "Getting started with Basic Memory" in result
    assert "write_note" in result
    # Offer-not-act.
    assert "Wait for them to say yes before writing" in result
    # Both paths present — the model chooses; the prompt does not decide emptiness for it.
    assert "If they have no notes yet" in result
    assert "If they already have notes" in result


@pytest.mark.asyncio
async def test_getting_started_keeps_selected_project_in_all_examples(client, test_project):
    """Every example call (first-note and continuation) must target the inspected project so
    onboarding never crosses into write_note's default project."""
    result = await getting_started(project=test_project.name)  # pyright: ignore[reportGeneralTypeIssues]

    name = test_project.name
    assert f'write_note(project="{name}"' in result
    assert f'search_notes(project="{name}"' in result
    assert f'read_note("permalink", project="{name}")' in result


@pytest.mark.asyncio
async def test_getting_started_no_project_omits_project_arg(client, test_project):
    """Without an explicit project, the example omits project and lets write_note resolve it."""
    result = await getting_started()  # pyright: ignore[reportGeneralTypeIssues]

    assert 'write_note(title="..."' in result
