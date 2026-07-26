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
from basic_memory.mcp.tools import recent_activity, search_notes, write_note
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
    assert "https://docs.basicmemory.com/llms.txt" in instructions
    assert "/raw/...md" in instructions
    # Offer-not-act: the model must be told not to write unprompted.
    assert "unprompted" in instructions


# --- recent_activity empty-state (lever 1b, primary) ---


@pytest.mark.asyncio
async def test_recent_activity_empty_project_offers_first_note(client, test_project):
    """An empty project (no test_graph) should surface the offer-not-act first-note guidance."""
    result = await recent_activity(project=test_project.name, timeframe="7d")  # pyright: ignore[reportGeneralTypeIssues]

    assert isinstance(result, str)
    assert "No recent activity" in result
    # The offer must be gated on the user agreeing first.
    assert "wait for them to agree" in result
    # Examples route by the collision-safe external id, and the widened call stays scoped.
    assert "write_note(project_id=" in result
    assert 'directory="notes"' in result
    assert "recent_activity(project_id=" in result
    assert 'timeframe="30d"' in result


@pytest.mark.asyncio
async def test_recent_activity_populated_project_has_no_first_note_offer(
    client, test_project, test_graph
):
    """A populated project must not nag with the first-note offer."""
    result = await recent_activity(project=test_project.name, timeframe="1w")  # pyright: ignore[reportGeneralTypeIssues]

    assert "wait for them to agree" not in result


@pytest.mark.asyncio
async def test_recent_activity_filtered_empty_result_has_no_first_note_offer(client, test_project):
    """A type-filter miss does not mean an established project has no notes."""
    await write_note(
        project=test_project.name,
        title="Existing note",
        content="This project already contains useful context.",
        directory="notes",
    )

    result = await recent_activity(
        project=test_project.name,
        type="relation",
        timeframe="7d",
    )

    assert isinstance(result, str)
    assert "No recent activity matched the requested type filter" in result
    assert "wait for them to agree" not in result
    assert "write_note(" not in result


@pytest.mark.asyncio
async def test_recent_activity_out_of_range_page_has_no_first_note_offer(
    client, test_project, test_graph
):
    """An empty later page should direct the agent back through pagination."""
    result = await recent_activity(
        project=test_project.name,
        timeframe="7d",
        page=100,
    )

    assert isinstance(result, str)
    assert "No recent activity was found on page 100" in result
    assert "Try page=99 or return to page=1" in result
    assert "wait for them to agree" not in result
    assert "write_note(" not in result


# --- search empty-state (lever 1b, delegates to recent_activity) ---


@pytest.mark.asyncio
async def test_search_no_results_points_to_recent_activity(client, test_project):
    """Empty search must point at recent_activity rather than repeat the first-note offer."""
    result = await search_notes(query="XYZ123NoSuchNote", project=test_project.name)  # pyright: ignore[reportGeneralTypeIssues]

    assert isinstance(result, str)
    assert "No results found" in result
    # The orientation call stays scoped to the searched project, by collision-safe external id.
    assert "recent_activity(project_id=" in result
    # It should NOT duplicate the write_note offer here (that would nag established users).
    assert "write_note" not in result


def test_search_empty_orientation_routes_like_the_search():
    """The empty-search orientation mirrors how the search was routed: an all-projects miss goes
    to the enumerator (a bare recent_activity() would narrow to the default project); a scoped
    search orients by external id, with the display name only as a local fallback."""
    empty = SearchResponse(results=[], current_page=1, page_size=10)

    # all-projects: must not suggest recent_activity() (not force-discovery); enumerate instead.
    all_projects = _format_search_markdown(empty, "all projects", "q")
    assert "list_memory_projects()" in all_projects
    assert "recent_activity(" not in all_projects

    routed = _format_search_markdown(empty, "myproj", "q", project_id="ext-9")
    assert 'recent_activity(project_id="ext-9")' in routed
    assert 'recent_activity(project="myproj")' not in routed

    local = _format_search_markdown(empty, "myproj", "q")
    assert 'recent_activity(project="myproj")' in local


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
async def test_getting_started_preserves_markdown_around_multiline_activity(monkeypatch):
    """Embedded activity must not turn the surrounding prompt into an indented code block."""

    async def fake_list_memory_projects(**_kwargs: object) -> dict[str, object]:
        return {
            "projects": [
                {
                    "name": "research",
                    "qualified_name": "personal/research",
                    "external_id": "project-1",
                    "is_default": True,
                    "workspace_is_default": True,
                }
            ],
            "default_project": "research",
            "constrained_project": None,
        }

    async def fake_recent_activity(**_kwargs: object) -> str:
        return "# Recent activity\n\n- Updated [[Project Plan]]"

    monkeypatch.setattr(
        "basic_memory.mcp.prompts.getting_started.list_memory_projects",
        fake_list_memory_projects,
    )
    monkeypatch.setattr(
        "basic_memory.mcp.prompts.getting_started.recent_activity",
        fake_recent_activity,
    )

    result = await getting_started(project="research")  # pyright: ignore[reportGeneralTypeIssues]

    assert result.startswith("# Getting started with Basic Memory")
    assert "\nBasic Memory gives the user" in result
    assert "\nHere is their recent activity:" in result
    assert "\n# Recent activity\n" in result
    assert "\nBased on what you see above:" in result


@pytest.mark.asyncio
async def test_getting_started_keeps_selected_project_in_all_examples(client, test_project):
    """Every example call (first-note and continuation) must target the inspected project so
    onboarding never crosses into write_note's default project."""
    result = await getting_started(project=test_project.name)  # pyright: ignore[reportGeneralTypeIssues]

    project_id = test_project.external_id
    assert f'write_note(project_id="{project_id}"' in result
    assert f'search_notes(project_id="{project_id}"' in result
    assert f'read_note(project_id="{project_id}", identifier="permalink")' in result
    assert 'directory="notes"' in result


@pytest.mark.asyncio
async def test_getting_started_no_project_uses_concrete_default_project(client, test_project):
    """Without an explicit project, onboarding resolves the default to its external id."""
    result = await getting_started()  # pyright: ignore[reportGeneralTypeIssues]

    assert f'write_note(project_id="{test_project.external_id}"' in result
    assert 'write_note(title="..."' not in result


@pytest.mark.asyncio
async def test_getting_started_prefers_constrained_project(monkeypatch):
    """A single-project server must ignore a conflicting prompt argument."""

    async def fake_list_memory_projects(**_kwargs: object) -> dict[str, object]:
        return {
            "projects": [
                {
                    "name": "locked",
                    "external_id": "project-locked",
                    "is_default": False,
                },
                {
                    "name": "requested",
                    "external_id": "project-requested",
                    "is_default": True,
                },
            ],
            "default_project": "requested",
            "constrained_project": "locked",
        }

    activity_calls: list[dict[str, object]] = []

    async def fake_recent_activity(**kwargs: object) -> str:
        activity_calls.append(kwargs)
        return "# Recent activity\n\nNo recent activity"

    monkeypatch.setattr(
        "basic_memory.mcp.prompts.getting_started.list_memory_projects",
        fake_list_memory_projects,
    )
    monkeypatch.setattr(
        "basic_memory.mcp.prompts.getting_started.recent_activity",
        fake_recent_activity,
    )

    result = await getting_started(  # pyright: ignore[reportGeneralTypeIssues]
        project="requested"
    )

    assert activity_calls == [{"timeframe": "30d", "project_id": "project-locked"}]
    assert 'write_note(project_id="project-locked"' in result
    assert "project-requested" not in result


@pytest.mark.asyncio
async def test_getting_started_preserves_merged_local_project_route(monkeypatch):
    """A configured mirror must win over a same-named default-workspace copy."""

    async def fake_list_memory_projects(**_kwargs: object) -> dict[str, object]:
        return {
            "projects": [
                {
                    "name": "main",
                    "qualified_name": "personal/main",
                    "external_id": "cloud-personal-main",
                    "source": "cloud",
                    "is_default": True,
                    "workspace_is_default": True,
                },
                {
                    "name": "main",
                    "qualified_name": "team/main",
                    "external_id": "cloud-team-main",
                    "source": "local+cloud",
                    "is_default": True,
                    "workspace_is_default": False,
                },
            ],
            "default_project": "main",
            "constrained_project": None,
        }

    activity_calls: list[dict[str, object]] = []

    async def fake_recent_activity(**kwargs: object) -> str:
        activity_calls.append(kwargs)
        return "# Recent activity\n\nNo recent activity"

    monkeypatch.setattr(
        "basic_memory.mcp.prompts.getting_started.list_memory_projects",
        fake_list_memory_projects,
    )
    monkeypatch.setattr(
        "basic_memory.mcp.prompts.getting_started.recent_activity",
        fake_recent_activity,
    )

    result = await getting_started()  # pyright: ignore[reportGeneralTypeIssues]

    assert activity_calls == [{"timeframe": "30d", "project": "main"}]
    assert 'write_note(project="main"' in result
    assert 'read_note(project="main"' in result
    assert 'search_notes(project="main"' in result
    assert "cloud-personal-main" not in result
    assert "cloud-team-main" not in result


@pytest.mark.asyncio
async def test_getting_started_without_projects_stops_before_note_actions(monkeypatch):
    """A fresh installation must establish a project before advertising note operations."""

    async def fake_list_memory_projects(**_kwargs: object) -> dict[str, object]:
        return {
            "projects": [],
            "default_project": None,
            "constrained_project": None,
        }

    async def fail_recent_activity(**_kwargs: object) -> str:
        raise AssertionError("recent_activity requires a selected project")

    monkeypatch.setattr(
        "basic_memory.mcp.prompts.getting_started.list_memory_projects",
        fake_list_memory_projects,
    )
    monkeypatch.setattr(
        "basic_memory.mcp.prompts.getting_started.recent_activity",
        fail_recent_activity,
    )

    result = await getting_started()  # pyright: ignore[reportGeneralTypeIssues]

    assert "No Basic Memory projects are available yet" in result
    assert "list_memory_projects()" in result
    assert 'recent_activity(project_id="...", timeframe="30d")' in result
    assert "invoke this getting-started prompt" not in result
    assert "write_note(" not in result
    assert "read_note(" not in result
    assert "search_notes(" not in result


@pytest.mark.asyncio
async def test_getting_started_requires_qualified_duplicate_project_selection(monkeypatch):
    """Duplicate cloud names pause onboarding, then the qualified choice routes by id."""

    async def fake_list_memory_projects(**_kwargs: object) -> dict[str, object]:
        return {
            "projects": [
                {
                    "name": "notes",
                    "qualified_name": "personal/notes",
                    "external_id": "project-personal",
                    "source": "local+cloud",
                    "is_default": False,
                    "workspace_is_default": True,
                },
                {
                    "name": "notes",
                    "qualified_name": "team/notes",
                    "external_id": "project-team",
                    "source": "cloud",
                    "is_default": False,
                    "workspace_is_default": False,
                },
            ],
            "default_project": None,
            "constrained_project": None,
        }

    activity_calls: list[dict[str, object]] = []

    async def fake_recent_activity(**kwargs: object) -> str:
        activity_calls.append(kwargs)
        return "# Recent activity\n\nNo recent activity"

    monkeypatch.setattr(
        "basic_memory.mcp.prompts.getting_started.list_memory_projects",
        fake_list_memory_projects,
    )
    monkeypatch.setattr(
        "basic_memory.mcp.prompts.getting_started.recent_activity",
        fake_recent_activity,
    )

    result = await getting_started(project="notes")  # pyright: ignore[reportGeneralTypeIssues]

    assert 'requested project "notes" did not identify one unique project' in result
    assert "`personal/notes`" in result
    assert "`team/notes`" in result
    assert 'project="notes"' in result
    assert 'project_id="project-team"' in result
    assert "`recent_activity(...)`" in result
    assert "invoke this getting-started prompt" not in result
    assert "write_note(" not in result
    assert "read_note(" not in result
    assert "search_notes(" not in result
    assert activity_calls == []

    selected_result = await getting_started(  # pyright: ignore[reportGeneralTypeIssues]
        project="team/notes"
    )

    assert activity_calls == [{"timeframe": "30d", "project_id": "project-team"}]
    assert 'write_note(project_id="project-team"' in selected_result
