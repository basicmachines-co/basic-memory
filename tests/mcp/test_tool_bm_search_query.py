"""bm_search query shape: modes, filters, tag shorthand, defaults and index readiness.

Ported from the pre-1.0 `search_notes` tests. The end-to-end cases run bm_search over
the real ASGI stack; the query-building cases call the shared, pure
`_build_search_query` / `split_tag_shorthand` / `_search_type_for` helpers that
bm_search runs every request through.
"""

import importlib
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from sqlalchemy import text as sa_text

from basic_memory import db
from basic_memory.config import ProjectMode
from basic_memory.runtime.mode import RuntimeMode
from basic_memory.mcp.container import McpContainer
from basic_memory.mcp.tools import bm_search, bm_write
from basic_memory.mcp.tools.search import (
    _build_search_query,
    _search_type_for,
    split_tag_shorthand,
)
from basic_memory.utils import parse_str_list

# The package exports the ChatGPT `search` adapter under the module's name.
search_module = importlib.import_module("basic_memory.mcp.tools.search")
container_module = importlib.import_module("basic_memory.mcp.container")


async def search_json(**arguments: Any) -> dict[str, Any]:
    result = await bm_search(output_format="json", **arguments)
    assert isinstance(result, dict)
    return result


async def _write_search_note(project_name: str) -> None:
    await bm_write(
        project=project_name,
        title="Test Search Note",
        directory="test",
        content="# Test\nThis is a searchable test note",
        tags=["test", "search"],
    )


def _query(**overrides: Any) -> dict[str, Any]:
    """`_build_search_query` arguments with nothing narrowing the search."""
    arguments: dict[str, Any] = {
        "query": None,
        "search_type": "text",
        "note_types": [],
        "entity_types": [],
        "categories": [],
        "after_date": None,
        "metadata_filters": None,
        "tags": None,
        "status": None,
        "min_similarity": None,
        "valid_at": None,
        "valid_overlaps": None,
        "time_kind": None,
    }
    arguments.update(overrides)
    return arguments


# --- Search modes, end to end ---


@pytest.mark.asyncio
async def test_title_mode_matches_the_title(client, test_project):
    await _write_search_note(test_project.name)

    payload = await search_json(query="Search Note", mode="title", project=test_project.name)

    assert [row["title"] for row in payload["results"]] == ["Test Search Note"]


@pytest.mark.asyncio
@pytest.mark.parametrize("qualified", [False, True])
async def test_permalink_mode_matches_an_exact_permalink(client, test_project, qualified):
    """A project-relative permalink is put into index form; a qualified one is kept."""
    await _write_search_note(test_project.name)
    permalink = "test/test-search-note"
    query = f"{test_project.permalink}/{permalink}" if qualified else permalink

    payload = await search_json(query=query, mode="permalink", project=test_project.name)

    assert [row["permalink"] for row in payload["results"]] == [
        f"{test_project.permalink}/{permalink}"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "total_is_exact"),
    [("text", True), ("semantic", False), ("hybrid", False)],
)
async def test_json_keeps_the_total_exactness_of_the_mode(
    client, test_project, mode, total_is_exact
):
    """Vector and hybrid searches skip the count query, so their total is not exact."""
    await _write_search_note(test_project.name)

    payload = await search_json(
        query="searchable", mode=mode, min_similarity=0.0, project=test_project.name
    )

    assert payload["results"]
    assert payload["total_is_exact"] is total_is_exact


# --- Filters, end to end ---


@pytest.mark.asyncio
async def test_note_types_filter_by_frontmatter_type(client, test_project):
    await bm_write(
        project=test_project.name,
        title="Note Type Test",
        directory="test",
        content="# Test\nFiltered by type",
    )

    matching = await search_json(
        query="filtered", mode="text", note_types=["note"], project=test_project.name
    )
    other = await search_json(
        query="filtered", mode="text", note_types=["person"], project=test_project.name
    )

    assert [row["title"] for row in matching["results"]] == ["Note Type Test"]
    assert other["results"] == []


@pytest.mark.asyncio
async def test_note_types_are_canonicalized_to_snake_case(client, test_project):
    """'Task Item', 'TaskItem' and 'task-item' all name the stored type task_item."""
    await bm_write(
        project=test_project.name,
        title="Canonical Task",
        directory="test",
        content="# Canonical Task\nCanonicalTypeToken",
        note_type="task_item",
    )

    for spelling in ("Task Item", "TaskItem", "task-item"):
        payload = await search_json(
            query="CanonicalTypeToken",
            mode="text",
            note_types=[spelling],
            project=test_project.name,
        )
        assert [row["title"] for row in payload["results"]] == ["Canonical Task"], spelling


@pytest.mark.asyncio
async def test_items_note_returns_only_whole_notes(client, test_project):
    await bm_write(
        project=test_project.name,
        title="Entity Type Test",
        directory="test",
        content="# Test\n- [fact] Filtered by type",
    )

    payload = await search_json(
        query="filtered", mode="text", items=["note"], project=test_project.name
    )

    assert payload["results"]
    assert all(row["type"] == "entity" for row in payload["results"])


@pytest.mark.asyncio
async def test_categories_filter_returns_only_the_exact_category(client, test_project):
    """A [decision] that mentions "requirement" is not a requirement (#430)."""
    await bm_write(
        project=test_project.name,
        title="Category Filter Note",
        directory="test",
        content=(
            "# Category Filter Note\n"
            "- [requirement] The system must enforce auth on every request\n"
            "- [decision] We deferred the auth requirement to next sprint\n"
        ),
    )

    requirements = await search_json(
        query="requirement",
        mode="text",
        items=["observation"],
        categories=["requirement"],
        project=test_project.name,
    )
    decisions = await search_json(
        query="requirement",
        mode="text",
        items=["observation"],
        categories=["decision"],
        project=test_project.name,
    )

    assert requirements["results"]
    assert all(row["category"] == "requirement" for row in requirements["results"])
    assert decisions["results"]
    assert all(row["category"] == "decision" for row in decisions["results"])


@pytest.mark.asyncio
async def test_categories_alone_return_observations(client, test_project):
    """Categories live on observations, so they imply observation results (#908)."""
    await bm_write(
        project=test_project.name,
        title="Category Default Note",
        directory="test",
        content=(
            "# Category Default Note\n"
            "- [requirement] Auth tokens must rotate every 24 hours\n"
            "- [decision] We chose JWT for the auth token format\n"
        ),
    )

    payload = await search_json(
        query="auth", mode="text", categories=["requirement"], project=test_project.name
    )

    assert payload["results"], "category-only search must return matching observations"
    assert all(row["type"] == "observation" for row in payload["results"])
    assert all(row["category"] == "requirement" for row in payload["results"])


@pytest.mark.asyncio
async def test_since_filters_by_update_time(client, test_project):
    await bm_write(
        project=test_project.name,
        title="Recent Note",
        directory="test",
        content="# Test\nRecent content",
    )

    recent = await search_json(
        query="recent",
        mode="text",
        since=(datetime.now() - timedelta(hours=1)).isoformat(),
        project=test_project.name,
    )
    future = await search_json(
        query="recent",
        mode="text",
        since=(datetime.now() + timedelta(days=1)).isoformat(),
        project=test_project.name,
    )

    assert [row["title"] for row in recent["results"]] == ["Recent Note"]
    assert future["results"] == []


@pytest.mark.asyncio
async def test_metadata_filter_note_type_key_reads_as_type(client, test_project):
    """`note_type` is the model column; the frontmatter key it means is `type` (#642)."""
    await bm_write(
        project=test_project.name,
        title="Spec Note",
        directory="test",
        content="# Spec Note\nAliasToken",
        note_type="spec",
        metadata={"priority": "high"},
    )

    payload = await search_json(
        filters={"note_type": "spec", "priority": "high"}, project=test_project.name
    )

    assert [row["title"] for row in payload["results"]] == ["Spec Note"]


# --- Tag shorthand, end to end ---


@pytest.mark.asyncio
async def test_tag_shorthand_filters_by_tag(client, test_project):
    await bm_write(
        project=test_project.name,
        title="Coffee Brewing",
        directory="test",
        content="# Coffee Brewing\nBrewToken pour over",
        tags=["coffee", "brewing"],
    )
    await bm_write(
        project=test_project.name,
        title="Coffee Beans",
        directory="test",
        content="# Coffee Beans\nBrewToken origins",
        tags=["coffee"],
    )

    by_tag = await search_json(query="tag:coffee", project=test_project.name)
    both_tags = await search_json(query="tag:coffee AND tag:brewing", project=test_project.name)
    tag_and_text = await search_json(
        query="origins tag:coffee", mode="text", project=test_project.name
    )

    assert {row["title"] for row in by_tag["results"]} == {"Coffee Brewing", "Coffee Beans"}
    assert [row["title"] for row in both_tags["results"]] == ["Coffee Brewing"]
    assert [row["title"] for row in tag_and_text["results"]] == ["Coffee Beans"]


# --- List parameters through the MCP layer ---


@pytest.mark.asyncio
async def test_list_parameters_accept_comma_and_json_strings_via_mcp(mcp, client, test_project):
    """'note,task' must filter like ['note', 'task'], not as one literal type (#930)."""
    await bm_write(
        project=test_project.name,
        title="Comma Split Note",
        directory="test",
        content="# Comma Split Note\n- [requirement] CommaSplitToken body",
    )

    async with Client(mcp) as mcp_client:

        async def titles(**arguments: Any) -> str:
            result = await mcp_client.call_tool(
                "bm_search",
                {"project": test_project.name, "query": "CommaSplitToken", "mode": "text"}
                | arguments,
            )
            return result.content[0].text

        assert "Comma Split Note" in await titles(note_types="note,task")
        assert "Comma Split Note" in await titles(note_types='["note", "task"]')
        assert "Comma Split Note" in await titles(note_types=["note,task"])
        assert "Comma Split Note" not in await titles(note_types="person")
        assert "Comma Split Note" in await titles(categories="requirement,decision")
        assert "Comma Split Note" not in await titles(categories="decision")


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["note_types", "categories"])
@pytest.mark.parametrize("value", [[42], ["note", 42]])
async def test_list_parameters_reject_non_string_elements_via_mcp(
    mcp, client, test_project, field, value
):
    """[42] is a caller mistake, not the type '42' that silently matches nothing."""
    async with Client(mcp) as mcp_client:
        with pytest.raises(ToolError):
            await mcp_client.call_tool(
                "bm_search", {"project": test_project.name, "query": "anything", field: value}
            )


def test_parse_str_list_returns_non_string_list_elements_for_validation():
    """parse_str_list must leave [42] as it is so Pydantic rejects it, not stringify it."""
    assert parse_str_list([42]) == [42]  # type: ignore[arg-type]
    assert parse_str_list(["ok", 42]) == ["ok", 42]  # type: ignore[arg-type]
    assert parse_str_list([{"a": 1}]) == [{"a": 1}]  # type: ignore[arg-type]
    assert parse_str_list(["note", "task"]) == ["note", "task"]
    assert parse_str_list(["note,task"]) == ["note", "task"]


# --- Default mode ---


@pytest.fixture
def search_config(app_config, monkeypatch):
    """Install the MCP container the server lifespan would, holding this test's config.

    The default mode is read from the container's config. The container is
    process-global and an earlier in-process MCP session leaves its own behind, so
    each test installs the one it reasons about.
    """
    monkeypatch.setattr(
        container_module, "_container", McpContainer(config=app_config, mode=RuntimeMode.TEST)
    )
    return app_config


@pytest.mark.asyncio
async def test_default_mode_is_hybrid_and_boolean_queries_run_as_text(
    client, test_project, search_config
):
    """Hybrid's vector half ignores NOT/AND, so a Boolean query defaults to full-text.

    The total's exactness tells the two apart: only full-text search counts.
    """
    await _write_search_note(test_project.name)

    plain = await search_json(query="searchable", project=test_project.name)
    boolean = await search_json(query="searchable NOT missing", project=test_project.name)

    assert plain["total_is_exact"] is False
    assert boolean["total_is_exact"] is True
    assert [row["title"] for row in boolean["results"]] == ["Test Search Note"]


@pytest.mark.asyncio
async def test_configured_default_search_type_wins(client, test_project, search_config):
    search_config.default_search_type = "text"
    await _write_search_note(test_project.name)

    payload = await search_json(query="searchable", project=test_project.name)

    assert payload["total_is_exact"] is True


def test_boolean_operators_are_recognized_only_as_standalone_uppercase_words(search_config):
    for query in ["coffee NOT pour", "pour AND clarity", "(tea OR coffee) NOT decaf"]:
        assert _search_type_for(None, query, ["test-project"]) == "text", query
    for query in ["coffee not pour", "ORACLE notes", "ANDROID", None]:
        assert _search_type_for(None, query, ["test-project"]) == "hybrid", query
    assert _search_type_for("hybrid", "coffee NOT pour", ["test-project"]) == "hybrid"


# Trigger: the host's sqlite-vec loads in the test environment.
# Why: the keyword-only fallback (#711) only shows on a host that cannot load it, so
#      these two tests inject that one fact; everything else runs for real.
def test_a_keyword_only_host_defaults_to_text(search_config, monkeypatch):
    monkeypatch.setattr(search_module, "semantic_runtime_available", lambda config: False)

    assert _search_type_for(None, "coffee", ["test-project"]) == "text"


def test_cloud_projects_default_to_hybrid_on_a_keyword_only_host(search_config, monkeypatch):
    """Cloud-routed projects search Cloud's Postgres, which always has vectors."""
    search_config.set_project_mode("test-project", ProjectMode.CLOUD)
    monkeypatch.setattr(search_module, "semantic_runtime_available", lambda config: False)

    assert _search_type_for(None, "coffee", ["test-project"]) == "hybrid"


# --- Query building ---


@pytest.mark.parametrize(
    ("search_type", "retrieval_mode"),
    [("text", "fts"), ("vector", "vector"), ("semantic", "vector"), ("hybrid", "hybrid")],
)
def test_search_types_map_to_retrieval_modes(search_type, retrieval_mode):
    search_query = _build_search_query(**_query(query="semantic lookup", search_type=search_type))

    assert search_query is not None
    assert search_query.text == "semantic lookup"
    assert search_query.retrieval_mode == retrieval_mode


def test_title_and_permalink_modes_fill_their_own_fields():
    title = _build_search_query(**_query(query="Meeting", search_type="title"))
    exact = _build_search_query(**_query(query="docs/meeting", search_type="permalink"))
    pattern = _build_search_query(**_query(query="docs/meeting-*", search_type="permalink"))

    assert title is not None and title.title == "Meeting"
    assert exact is not None and exact.permalink == "docs/meeting"
    assert pattern is not None and pattern.permalink_match == "docs/meeting-*"


def test_an_unknown_search_type_is_an_error():
    with pytest.raises(ValueError, match="Invalid search_type 'bogus'"):
        _build_search_query(**_query(query="test", search_type="bogus"))


def test_filters_reach_the_query():
    search_query = _build_search_query(
        **_query(
            query="test",
            search_type="vector",
            metadata_filters={"note_type": "spec", "priority": "high"},
            tags=["important"],
            status="published",
            min_similarity=0.0,
        )
    )

    assert search_query is not None
    assert search_query.metadata_filters == {"type": "spec", "priority": "high"}
    assert search_query.tags == ["important"]
    assert search_query.status == "published"
    assert search_query.min_similarity == 0.0


def test_a_filter_alone_is_enough_criteria_and_nothing_is_none():
    filter_only = _build_search_query(**_query(metadata_filters={"status": "in-progress"}))

    assert filter_only is not None
    assert filter_only.text is None and filter_only.title is None
    assert filter_only.permalink is None
    assert _build_search_query(**_query()) is None


def test_results_default_to_whole_notes_unless_items_say_otherwise():
    """Observations and relations stay out of a plain search (#31)."""
    default = _build_search_query(**_query(query="test"))
    explicit = _build_search_query(**_query(query="test", entity_types=["observation"]))

    assert default is not None and default.entity_types == ["entity"]
    assert explicit is not None and explicit.entity_types == ["observation"]


@pytest.mark.parametrize(
    ("query", "tags", "expected_query", "expected_tags"),
    [
        ("tag:security", None, None, {"security"}),
        ("tag:security", ["oauth"], None, {"security", "oauth"}),
        ("tag:coffee AND tag:brewing", None, None, {"coffee", "brewing"}),
        ("tag:coffee,brewing", None, None, {"coffee", "brewing"}),
        ("authentication tag:security", None, "authentication", {"security"}),
        ("no tags here", None, "no tags here", None),
        (None, None, None, None),
    ],
)
def test_tag_shorthand_moves_tags_out_of_the_query(query, tags, expected_query, expected_tags):
    remaining_query, merged_tags = split_tag_shorthand(query, tags)

    assert remaining_query == expected_query
    assert (set(merged_tags) if merged_tags is not None else None) == expected_tags


# --- Index readiness ---


async def _forget_indexing(session_maker, config_home, project_id: int) -> None:
    """Files on disk, but no index pass has ever completed."""
    notes_dir = config_home / "notes"
    notes_dir.mkdir(exist_ok=True)
    (notes_dir / "unindexed-note.md").write_text("# Unindexed Note\n\nNo index covers this.\n")
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            sa_text("UPDATE project SET last_indexed_at = NULL WHERE id = :id"),
            {"id": project_id},
        )


@pytest.mark.asyncio
async def test_a_never_indexed_miss_says_so(client, test_project, session_maker, config_home):
    """A never-indexed project must not report an ordinary empty result (#1534)."""
    await _forget_indexing(session_maker, config_home, test_project.id)

    text = await bm_search(query="unindexed-note-xyzzy", mode="text", project=test_project.name)
    payload = await search_json(
        query="unindexed-note-xyzzy", mode="text", project=test_project.name
    )

    assert isinstance(text, str)
    assert "never been indexed" in text
    assert "bm project index" in text
    assert payload["results"] == []
    assert payload["guidance"].startswith("# Project Index Required")


@pytest.mark.asyncio
async def test_an_indexed_miss_is_an_ordinary_empty_result(client, test_project, session_maker):
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            sa_text("UPDATE project SET last_indexed_at = :now WHERE id = :id"),
            {"now": datetime.now(), "id": test_project.id},
        )
    await bm_write(
        project=test_project.name,
        title="Indexed Note",
        directory="notes",
        content="# Indexed Note\n\nSearchable content about telescopes.\n",
    )

    miss = await bm_search(
        query="nothing-matches-this-xyzzy", mode="text", project=test_project.name
    )
    hit = await search_json(query="telescopes", mode="text", project=test_project.name)

    assert miss == f"No results for 'nothing-matches-this-xyzzy' in project '{test_project.name}'."
    assert hit["results"]
    assert "guidance" not in hit
