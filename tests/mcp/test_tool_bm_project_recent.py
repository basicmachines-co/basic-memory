"""bm_project(command="recent"): what changed lately in one project.

Tool calls run the real ASGI stack through the shared `client` fixture; argument
validation runs through the MCP server, where the published schema applies. The text
formatter is also exercised directly with hand-built graphs for shapes a small test
project cannot produce cheaply (many rows, long observations, a next page).
"""

import re
from datetime import datetime, timezone
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from sqlalchemy import text as sa_text

from basic_memory import db
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools import bm_project
from basic_memory.mcp.tools.recent_activity import _format_project_output, _truncate_at_word
from basic_memory.schemas.memory import (
    ContextResult,
    EntitySummary,
    GraphContext,
    MemoryMetadata,
    ObservationSummary,
    RelationSummary,
)


async def recent_text(project: str, **arguments: Any) -> str:
    result = await bm_project(command="recent", project=project, **arguments)
    assert isinstance(result, str)
    return result


async def recent_rows(project: str, **arguments: Any) -> list[dict[str, Any]]:
    result = await bm_project(command="recent", project=project, output_format="json", **arguments)
    assert isinstance(result, dict)
    return result["results"]


# --- Tool calls ---


@pytest.mark.asyncio
@pytest.mark.parametrize("since", ["7d", "yesterday", "0d"])
async def test_since_accepts_relative_and_natural_language(client, test_project, test_graph, since):
    text = await recent_text(test_project.name, items=["note"], since=since)

    assert "Recent Activity:" in text
    assert since in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"since": "invalid"},
        {"items": ["bogus"]},
        {"page": 0},
        {"page_size": 0},
        {"page_size": 999},
    ],
)
async def test_invalid_arguments_are_refused_through_mcp(client, test_project, arguments):
    async with Client(mcp) as mcp_client:
        with pytest.raises(ToolError):
            await mcp_client.call_tool(
                "bm_project", {"command": "recent", "project": test_project.name, **arguments}
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "items", [["note"], ["note", "observation"], ["note", "observation", "relation"]]
)
async def test_item_filters_render_sections(client, test_project, test_graph, items):
    text = await recent_text(test_project.name, items=items)

    assert "Recent Activity:" in text
    assert "Recent Notes & Documents" in text
    assert "Activity Summary:" in text


@pytest.mark.asyncio
async def test_notes_only_by_default(client, test_project, test_graph):
    """One well-connected note must not fill the page with its observations and relations."""
    rows = await recent_rows(test_project.name)

    assert rows
    assert {row["type"] for row in rows} == {"entity"}


@pytest.mark.asyncio
async def test_explicit_items_return_those_kinds(client, test_project, test_graph):
    observations = await recent_rows(test_project.name, items=["observation"])
    relations = await recent_rows(test_project.name, items=["relation"])
    mixed = await recent_rows(test_project.name, items=["note", "observation", "relation"])

    assert observations and {row["type"] for row in observations} == {"observation"}
    assert relations and {row["type"] for row in relations} == {"relation"}
    assert "entity" in {row["type"] for row in mixed}
    for row in observations + relations + mixed:
        assert set(row) == {"type", "title", "permalink", "file_path", "created_at"}


@pytest.mark.asyncio
async def test_text_page_names_the_next_page(client, test_project, test_graph):
    text = await recent_text(test_project.name, items=["note"], page=1, page_size=2)

    assert "Showing 2 items (page 1). Use page=2 to see more." in text


@pytest.mark.asyncio
async def test_note_rows_carry_the_external_id(client, test_graph, test_project):
    """The hosted MCP link template has agents substitute this id, so text shows it."""
    text = await recent_text(test_project.name, since="30d")

    uuid_pattern = r"\[id: [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\]"
    note_lines = [line for line in text.splitlines() if line.strip().startswith("•")]
    assert note_lines, text
    assert any(re.search(uuid_pattern, line) for line in note_lines), note_lines


@pytest.mark.asyncio
async def test_never_indexed_project_says_so(client, test_project, session_maker, config_home):
    """A never-indexed project must not report an ordinary empty activity feed (#1534)."""
    notes_dir = config_home / "notes"
    notes_dir.mkdir(exist_ok=True)
    (notes_dir / "unindexed-note.md").write_text("# Unindexed Note\n\nNot yet indexed.\n")
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            sa_text("UPDATE project SET last_indexed_at = NULL WHERE id = :id"),
            {"id": test_project.id},
        )

    text = await recent_text(test_project.name)
    payload = await bm_project(command="recent", project=test_project.name, output_format="json")

    assert "never been indexed" in text
    assert "bm project index" in text
    assert isinstance(payload, dict)
    assert payload["results"] == []
    assert "never been indexed" in payload["guidance"]


@pytest.mark.asyncio
async def test_indexed_empty_project_offers_a_first_note(
    client, test_project, session_maker, config_home
):
    """An indexed-but-quiet project keeps the onboarding copy, routed by external id (#1534)."""
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            sa_text("UPDATE project SET last_indexed_at = :now WHERE id = :id"),
            {"now": datetime.now(), "id": test_project.id},
        )

    text = await recent_text(test_project.name)

    assert f"No recent activity in '{test_project.name}' within 7d" in text
    assert "never been indexed" not in text
    assert "wait for them to agree" in text
    # Names collide across cloud workspaces; external ids do not.
    route = f'project="{test_project.external_id}"'
    assert f"bm_write({route}" in text
    assert f'bm_project(command="recent", {route}' in text
    assert f"bm_search({route}" in text


# --- Text formatting ---


def _empty_graph() -> GraphContext:
    return GraphContext(
        results=[], metadata=MemoryMetadata(depth=1, generated_at=datetime.now(timezone.utc))
    )


def _entity_result(index: int, title: str) -> ContextResult:
    return ContextResult(
        primary_result=EntitySummary(
            external_id=f"550e8400-e29b-41d4-a716-44665544{index:04d}",
            entity_id=index,
            permalink=f"notes/entity-{index}",
            title=title,
            content=None,
            file_path=f"notes/entity-{index}.md",
            created_at=datetime.now(timezone.utc),
        ),
        observations=[],
        related_results=[],
    )


def _format(activity: GraphContext, *, page: int = 1, type_filter_applied: bool = False) -> str:
    return _format_project_output(
        "proj",
        activity,
        "7d",
        page=page,
        project_id="ext-123",
        type_filter_applied=type_filter_applied,
    )


def test_filtered_empty_page_has_no_first_note_offer():
    out = _format(_empty_graph(), type_filter_applied=True)

    assert "No recent activity matched the requested type filter" in out
    assert "omit `items`" in out
    assert "bm_write" not in out


def test_later_empty_page_has_no_first_note_offer():
    out = _format(_empty_graph(), page=3)

    assert "No recent activity was found on page 3" in out
    assert "Try page=2 or return to page=1" in out
    assert "bm_write" not in out


def test_every_returned_row_is_rendered():
    """Regression for #784: the heading count and the rendered rows always agree."""
    now = datetime.now(timezone.utc)
    entity_titles = [f"Entity {i}" for i in range(15)]
    relations = [
        ContextResult(
            primary_result=RelationSummary(
                relation_id=100 + i,
                entity_id=i,
                title=f"Relation {i}",
                file_path=f"notes/entity-{i}.md",
                permalink=f"notes/entity-{i}",
                relation_type="references",
                from_entity=f"Entity {i}",
                to_entity=f"Entity {i + 1}",
                created_at=now,
            ),
            observations=[],
            related_results=[],
        )
        for i in range(12)
    ]
    activity = GraphContext(
        results=[_entity_result(i, title) for i, title in enumerate(entity_titles)] + relations,
        metadata=MemoryMetadata(depth=1, generated_at=now),
    )

    out = _format(activity)

    for title in entity_titles:
        assert title in out
    for i in range(12):
        assert f"[[Entity {i}]] → references → [[Entity {i + 1}]]" in out
    assert "Recent Notes & Documents (15)" in out
    assert "Recent Connections (12)" in out
    assert "27 items found." in out
    assert "Use page=" not in out


def test_long_observations_are_truncated():
    activity = GraphContext(
        results=[
            ContextResult(
                primary_result=ObservationSummary(
                    observation_id=1,
                    entity_id=1,
                    title="Obs",
                    file_path="notes/obs.md",
                    permalink="notes/obs",
                    category="test",
                    content="This is a very long observation " * 10,
                    created_at=datetime.now(timezone.utc),
                ),
                observations=[],
                related_results=[],
            )
        ],
        metadata=MemoryMetadata(depth=1, generated_at=datetime.now(timezone.utc)),
    )

    out = _format(activity)

    assert "Recent Observations (1)" in out
    assert "**test:** 1 items" in out
    assert "..." in out


def test_truncate_at_word():
    assert _truncate_at_word("short", 80) == "short"
    assert _truncate_at_word("word " * 40, 80).endswith("word...")
    assert _truncate_at_word("x" * 200, 80) == "x" * 77 + "..."


def test_a_next_page_is_named():
    now = datetime.now(timezone.utc)
    activity = GraphContext(
        results=[_entity_result(1, "Test Note")],
        metadata=MemoryMetadata(depth=1, generated_at=now),
        has_more=True,
    )

    out = _format(activity)

    assert "Showing 1 items (page 1). Use page=2 to see more." in out
