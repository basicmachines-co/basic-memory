"""bm_context: graph context from a note or pattern, routed like every bm_* tool (#1830).

These run the real ASGI stack through the shared `client` fixture.
"""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from basic_memory.mcp.server import mcp
from basic_memory.mcp.tools import bm_context, bm_read, bm_write
from basic_memory.mcp.tools.context import (
    _compact_context_labels,
    _format_context_markdown,
    _format_entity_block,
)
from basic_memory.schemas.memory import (
    MAX_CONTEXT_DEPTH,
    ContextResult,
    EntitySummary,
    GraphContext,
    MemoryMetadata,
    ObservationSummary,
    RelationSummary,
)


async def _seed(project_name: str) -> None:
    await bm_write(
        title="Node A",
        directory="graph",
        content="# Node A\n\n- [fact] A starts the chain\n\n- links_to [[Node B]]",
        project=project_name,
    )
    await bm_write(
        title="Node B",
        directory="graph",
        content="# Node B\n\n- [fact] B is in the middle\n\n- links_to [[Node A]]",
        project=project_name,
    )


async def context_json(**arguments: Any) -> dict[str, Any]:
    result = await bm_context(**arguments)
    assert isinstance(result, dict)
    return result


@pytest.mark.asyncio
async def test_json_by_default_with_related_notes(client, test_project):
    await _seed(test_project.name)

    graph = await context_json(identifier="graph/node-a", project=test_project.name)

    (result,) = graph["results"]
    assert result["primary_result"]["title"] == "Node A"
    related_titles = {row.get("title") for row in result["related_results"]}
    assert "Node B" in related_titles


@pytest.mark.asyncio
async def test_explicit_project_keeps_the_identifier_a_path(client, test_project, caplog):
    # #1814: the first segment ("graph") was resolved as a project, logging a
    # client error on every call.
    await _seed(test_project.name)

    graph = await context_json(identifier="memory://graph/node-a", project=test_project.name)

    assert graph["results"][0]["primary_result"]["title"] == "Node A"
    assert "Project not found: 'graph'" not in caplog.text


@pytest.mark.asyncio
async def test_pattern_and_compact_text(client, test_project):
    await _seed(test_project.name)

    # #1812: a project-relative pattern, though permalinks here carry the project prefix.
    text = await bm_context(
        identifier="graph/*",
        compact=True,
        output_format="text",
        project=test_project.name,
    )

    assert isinstance(text, str)
    assert "Node A" in text and "Node B" in text


@pytest.mark.asyncio
async def test_compact_json_drops_bodies(client, test_project):
    await _seed(test_project.name)

    graph = await context_json(identifier="graph/node-a", compact=True, project=test_project.name)

    primary = graph["results"][0]["primary_result"]
    assert "content" not in primary
    assert "observations" not in graph["results"][0]


@pytest.mark.asyncio
async def test_an_identifier_cannot_reach_outside_the_project(client, test_project):
    """Identifiers resolve inside the project: a '../' spelling finds a project note."""
    await _seed(test_project.name)

    graph = await context_json(identifier="../graph/node-a", project=test_project.name)

    paths = [result["primary_result"]["file_path"] for result in graph["results"]]
    assert paths == ["graph/Node A.md"]


@pytest.mark.asyncio
async def test_project_qualified_start_returns_qualified_paths(client, test_project):
    """Every path in the graph can be passed back to bm_read."""
    await _seed(test_project.name)
    prefix = test_project.permalink

    graph = await context_json(identifier=f"{prefix}/graph/node-a", project=test_project.name)

    (result,) = graph["results"]
    paths = [result["primary_result"]["file_path"]]
    paths += [row["file_path"] for row in result["observations"]]
    paths += [row["file_path"] for row in result["related_results"]]
    assert paths and all(path.startswith(f"{prefix}/graph/") for path in paths)


@pytest.mark.asyncio
async def test_project_qualified_text_names_qualified_paths(client, test_project):
    """Text output carries the qualified paths too; a permalink may lack the prefix."""
    await _seed(test_project.name)
    prefix = test_project.permalink

    text = await bm_context(
        identifier=f"{prefix}/graph/node-a", output_format="text", project=test_project.name
    )

    assert isinstance(text, str)
    assert f"path: {prefix}/graph/Node A.md" in text
    assert f"[[Node B]] ({prefix}/graph/Node B.md)" in text


@pytest.mark.asyncio
async def test_text_output_names_the_next_page(client, test_project):
    """A pattern with more matches than a page says how to fetch the next one."""
    await _seed(test_project.name)

    text = await bm_context(
        identifier="graph/*", page_size=1, output_format="text", project=test_project.name
    )

    assert isinstance(text, str)
    assert "More results available. Call bm_context(identifier='graph/*'" in text
    assert "page=2, page_size=1)" in text


@pytest.mark.asyncio
async def test_malformed_locator_is_actionable_input_feedback(client, test_project):
    """The production-shaped locator must not surface as an opaque server failure (#1879)."""
    locator = "workspace/project/https://example.invalid/notes"

    async with Client(mcp) as mcp_client:
        with pytest.raises(ToolError) as exc_info:
            await mcp_client.call_tool(
                "bm_context",
                {"identifier": locator, "project": test_project.name},
            )

    message = str(exc_info.value)
    assert "protocol scheme" in message
    assert "Internal server error" not in message


# --- Graph shape (ported from the pre-1.0 build_context tests) ---


@pytest.mark.asyncio
async def test_basic_context_fields(client, test_graph, test_project):
    """JSON output carries the primary note, its relations, and the metadata."""
    result = await context_json(identifier="memory://test/root", project=test_project.name)

    assert len(result["results"]) == 1
    primary = result["results"][0]["primary_result"]
    assert primary["permalink"] == f"{test_project.name}/test/root"
    assert len(result["results"][0]["related_results"]) > 0

    meta = result["metadata"]
    assert meta["uri"] == f"{test_project.name}/test/root"
    assert meta["depth"] == 1
    assert meta["timeframe"] is not None
    assert meta["primary_count"] == 1
    # COMPAT(v0.18): generated_at and total_results restored for old clients
    assert "generated_at" in meta
    assert "total_results" in meta

    assert "entity_id" in primary
    assert "created_at" in primary

    for obs in result["results"][0]["observations"]:
        for field in (
            "observation_id",
            "entity_id",
            "file_path",
            "created_at",
            "permalink",
            "category",
            "content",
        ):
            assert field in obs

    for related in result["results"][0]["related_results"]:
        if related["type"] == "entity":
            fields = ("title", "file_path", "created_at", "entity_id")
        else:
            fields = ("relation_type", "title", "file_path", "created_at", "relation_id")
        for field in fields:
            assert field in related


@pytest.mark.asyncio
async def test_pattern_matches_many_notes(client, test_graph, test_project):
    result = await context_json(identifier="memory://test/*", project=test_project.name)

    assert len(result["results"]) > 1
    assert all(
        f"{test_project.name}/test/" in item["primary_result"]["permalink"]
        for item in result["results"]
    )
    assert result["metadata"]["depth"] == 1


@pytest.mark.asyncio
async def test_external_id_keeps_workspace_canonical_path(app, test_project):
    """An external-id route keeps the ContextVar workspace prefix in memory URL lookups."""
    from basic_memory.workspace_context import workspace_permalink_context

    with workspace_permalink_context(workspace_slug="team-paul", workspace_type="organization"):
        await bm_write(
            project=test_project.external_id,
            title="Workspace Build Context Note",
            directory="tests",
            content="Build context should find this workspace note",
        )

        result = await context_json(
            identifier="memory://tests/*", since="30d", project=test_project.external_id
        )

    assert len(result["results"]) == 1
    primary = result["results"][0]["primary_result"]
    assert primary["permalink"] == (
        f"team-paul/{test_project.name}/tests/workspace-build-context-note"
    )
    assert primary["content"] == "Build context should find this workspace note"


@pytest.mark.asyncio
async def test_wider_window_never_returns_fewer_related(client, test_graph, test_project):
    recent = await context_json(
        identifier="memory://test/root", since="1d", project=test_project.name
    )
    older = await context_json(
        identifier="memory://test/root", since="30d", project=test_project.name
    )

    def related_count(graph: dict[str, Any]) -> int:
        return sum(len(item["related_results"]) for item in graph["results"])

    assert related_count(older) >= related_count(recent)


@pytest.mark.asyncio
async def test_missing_note_is_an_empty_graph(client, test_project):
    result = await context_json(
        identifier="memory://test/does-not-exist", project=test_project.name
    )

    assert result["results"] == []
    assert result["metadata"]["primary_count"] == 0
    assert result["metadata"]["related_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("since", ["7d", "yesterday", "0d"])
async def test_since_accepts_relative_and_natural_language(client, test_graph, test_project, since):
    result = await context_json(
        identifier="memory://specs/test", since=since, project=test_project.name
    )

    assert "results" in result


@pytest.mark.asyncio
async def test_since_rejects_nonsense_through_mcp(client, test_project):
    async with Client(mcp) as mcp_client:
        with pytest.raises(ToolError):
            await mcp_client.call_tool(
                "bm_context",
                {
                    "identifier": "memory://specs/test",
                    "since": "invalid",
                    "project": test_project.name,
                },
            )


@pytest.mark.asyncio
async def test_string_depth_is_coerced_through_mcp(client, test_graph, test_project):
    async with Client(mcp) as mcp_client:
        result = await mcp_client.call_tool(
            "bm_context",
            {"identifier": "memory://test/root", "depth": "2", "project": test_project.name},
        )

    graph = json.loads(result.content[0].text)
    assert graph["metadata"]["depth"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("depth", [MAX_CONTEXT_DEPTH + 1, str(MAX_CONTEXT_DEPTH + 1), -1, "deep"])
async def test_depth_outside_bounds_is_refused_through_mcp(client, test_project, depth):
    """Deep traversals fail at the tool boundary with the bound the API enforces."""
    async with Client(mcp) as mcp_client:
        with pytest.raises(ToolError):
            await mcp_client.call_tool(
                "bm_context",
                {"identifier": "memory://test/root", "depth": depth, "project": test_project.name},
            )


@pytest.mark.asyncio
async def test_max_depth_is_inclusive(client, test_graph, test_project):
    """The maximum depth reaches the API without a 422."""
    result = await context_json(
        identifier="memory://test/root", depth=MAX_CONTEXT_DEPTH, project=test_project.name
    )

    assert result["metadata"]["depth"] == MAX_CONTEXT_DEPTH


@pytest.mark.asyncio
async def test_text_output_for_one_note(client, test_graph, test_project):
    text = await bm_context(
        identifier="memory://test/root", output_format="text", project=test_project.name
    )

    assert isinstance(text, str)
    assert "# Context: Root" in text
    assert "1 primary" in text
    assert f"project: {test_project.name}" in text


@pytest.mark.asyncio
async def test_compact_skips_observations(client, test_graph, test_project):
    """Compact asks the server not to load observations and lists none (#1571)."""
    full = await context_json(identifier="memory://test/root", project=test_project.name)
    compact = await context_json(
        identifier="memory://test/root", compact=True, project=test_project.name
    )

    assert [obs["content"] for obs in full["results"][0]["observations"]] == [
        "Root note 1",
        "Root tech note",
    ]
    assert full["metadata"]["total_observations"] > 0

    # The server counted no observations, so the tool sent include_observations=False,
    # and compact JSON drops the list entirely.
    assert compact["metadata"]["total_observations"] == 0
    assert "observations" not in compact["results"][0]
    assert "content" not in compact["results"][0]["primary_result"]
    assert (
        compact["results"][0]["primary_result"]["permalink"]
        == full["results"][0]["primary_result"]["permalink"]
    )
    assert len(compact["results"][0]["related_results"]) == len(
        full["results"][0]["related_results"]
    )


@pytest.mark.asyncio
async def test_text_output_for_a_pattern(client, test_graph, test_project):
    """Many results take the URI as the title and separate the note blocks."""
    text = await bm_context(
        identifier="memory://test/*", output_format="text", project=test_project.name
    )

    assert isinstance(text, str)
    assert "# Context: " in text
    assert "# Context: Root\n" not in text
    assert "\n\n---\n\n" in text
    assert "primary" in text


@pytest.mark.asyncio
async def test_text_output_for_a_missing_note(client, test_project):
    text = await bm_context(
        identifier="memory://test/does-not-exist", output_format="text", project=test_project.name
    )

    assert isinstance(text, str)
    assert "No results found" in text
    assert test_project.name in text


def test_unresolved_relations_render_by_name():
    """Unresolved forward references render their target text, not [[None]] (#955)."""
    now = datetime.now(UTC)
    page = EntitySummary(
        external_id="entity-1",
        entity_id=1,
        title="write-note(3)",
        permalink="man3/write-note-3",
        content="# write-note(3)",
        file_path="man3/write-note-3.md",
        created_at=now,
    )
    unresolved = RelationSummary(
        title="see_also: edit-note(3)",
        file_path="man3/write-note-3.md",
        permalink="man3/write-note-3/see-also/edit-note-3",
        relation_type="see_also",
        from_entity="write-note(3)",
        from_entity_external_id="entity-1",
        to_entity=None,
        to_name="edit-note(3)",
        created_at=now,
    )
    resolved = RelationSummary(
        title="see_also: bm-note(5)",
        file_path="man3/write-note-3.md",
        permalink="man3/write-note-3/see-also/bm-note-5",
        relation_type="see_also",
        from_entity="write-note(3)",
        from_entity_external_id="entity-1",
        to_entity="bm-note(5)",
        to_name="bm-note(5)",
        created_at=now,
    )
    block = _format_entity_block(
        ContextResult(primary_result=page, observations=[], related_results=[unresolved, resolved])
    )

    assert "- see_also [[edit-note(3)]]" in block
    assert "- see_also [[bm-note(5)]]" in block
    assert "[[None]]" not in block


def test_compact_observation_labels_use_the_owning_file():
    """Content-derived observation labels must not reintroduce omitted prose."""
    observation = ObservationSummary(
        observation_id=12,
        entity_external_id="source-uuid",
        title="fact: Private source prose",
        file_path="notes/Source.md",
        permalink="notes/source/observations/fact/private-source-prose",
        category="fact",
        content="Private source prose",
        created_at=datetime.now(UTC),
    )
    graph = GraphContext(
        results=[ContextResult(primary_result=observation, related_results=[observation])],
        metadata=MemoryMetadata(depth=1),
    )

    compact = _compact_context_labels(graph)

    for item in [compact.results[0].primary_result, *compact.results[0].related_results]:
        assert isinstance(item, ObservationSummary)
        assert item.title == "fact"
        assert item.permalink == "notes/Source.md"
        assert item.entity_external_id == "source-uuid"
        assert item.observation_id == 12
    text = _format_context_markdown(compact, "project", compact=True)
    assert "notes/Source.md" in text
    assert "private" not in text.lower()
    # The input graph is not mutated.
    assert graph.results[0].primary_result.title == "fact: Private source prose"


@pytest.mark.asyncio
async def test_text_names_the_source_of_incoming_relations(client, test_project):
    """An incoming relation names its source instead of reading as a self-link (#1717)."""
    await bm_write(
        project=test_project.name,
        title="Bean Origins",
        directory="coffee",
        content="# Bean Origins\n\n- informs [[Brewing Notes]]\n",
    )
    await bm_write(
        project=test_project.name,
        title="Brewing Notes",
        directory="coffee",
        content="# Brewing Notes\n\n- relates_to [[Bean Origins]]\n",
    )

    text = await bm_context(
        identifier="memory://coffee/bean-origins", output_format="text", project=test_project.name
    )

    assert isinstance(text, str)
    # Owned by the primary note: unchanged, bare form.
    assert "- informs [[Brewing Notes]]" in text
    # Incoming: the source is named, so it no longer reads as Bean Origins -> itself.
    assert "- [[Brewing Notes]] relates_to [[Bean Origins]]" in text
    assert "\n- relates_to [[Bean Origins]]" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identifier_template",
    [
        "memory://s/2026/09/ses_AbCdEfGhIj",
        "memory://{project}/s/2026/09/ses_AbCdEfGhIj",
        "s/2026/09/ses_AbCdEfGhIj",
    ],
)
async def test_finds_explicit_non_slug_permalink(client, test_project, identifier_template):
    """A verbatim frontmatter permalink resolves through every spelling (#1549)."""
    permalink = "s/2026/09/ses_AbCdEfGhIj"
    await bm_write(
        project=test_project.name,
        title="index",
        directory="s/2026/09/sessions",
        content=f"---\npermalink: {permalink}\n---\n\n# T\n\nBody.\n",
    )
    identifier = identifier_template.format(project=test_project.name)

    result = await context_json(identifier=identifier, project=test_project.name)
    assert result["metadata"]["primary_count"] == 1
    assert result["results"][0]["primary_result"]["permalink"] == permalink

    note = await bm_read(identifier=identifier, project=test_project.name, output_format="json")
    assert isinstance(note, dict)
    assert note["permalink"] == permalink


@pytest.mark.asyncio
async def test_miss_returns_nothing(client, test_project):
    """A memory:// URL is an address; a miss must not become a fuzzy guess (#1626)."""
    for title in ("Search Spec", "Cache Layer Design"):
        await bm_write(
            project=test_project.name,
            title=title,
            directory="specs",
            content=f"# {title}\n\nNotes about {title.lower()}.\n\n- relates_to [[Search Spec]]\n",
        )

    for identifier in (
        "memory://zzq-nonexistent",
        f"memory://{test_project.name}/zzq-nonexistent",
    ):
        result = await context_json(identifier=identifier, project=test_project.name)
        assert result["metadata"]["primary_count"] == 0, identifier
        assert result["results"] == []

    titled = await context_json(identifier="memory://Cache Layer Design", project=test_project.name)
    assert [r["primary_result"]["title"] for r in titled["results"]] == ["Cache Layer Design"]


@pytest.mark.asyncio
async def test_refuses_ambiguous_title(client, test_project):
    """A title naming two notes is refused, as bm_read refuses it, not guessed."""
    for directory in ("coffee", "archive"):
        await bm_write(
            project=test_project.name,
            title="Bean Origins",
            directory=directory,
            content=f"# Bean Origins\n\nThe {directory} copy.\n",
        )

    with pytest.raises(ToolError, match="Ambiguous identifier 'Bean Origins'"):
        await bm_context(identifier="memory://Bean Origins", project=test_project.name)

    exact = await context_json(
        identifier="memory://archive/bean-origins", project=test_project.name
    )
    assert [r["primary_result"]["file_path"] for r in exact["results"]] == [
        "archive/Bean Origins.md"
    ]


@pytest.mark.asyncio
async def test_timestamps_all_carry_an_offset(client, test_graph, test_project):
    """Primary and related results report created_at in one shape, offset included."""
    result = await context_json(identifier="memory://test/root", project=test_project.name)

    item = result["results"][0]
    timestamps = [item["primary_result"]["created_at"]] + [
        related["created_at"] for related in item["related_results"]
    ]
    assert len(timestamps) > 1
    for timestamp in timestamps:
        assert datetime.fromisoformat(timestamp).tzinfo is not None, timestamp
