"""Shared bm_* parameter types and refusals (#1829).

The alias, bound, and coercion behavior only exists on a real tool call, so most of
these tests register a probe tool on a throwaway FastMCP server and call it the way an
MCP client does.
"""

import json
from typing import Any

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from basic_memory.mcp.refusals import refusal
from basic_memory.mcp.tool_params import (
    Filters,
    Items,
    NoteIdentifier,
    Page,
    PageSize,
    ProjectById,
    ProjectByName,
    ProjectParam,
    Since,
    parse_project,
    routing_arguments,
    search_item_types,
)
from basic_memory.schemas.search import SearchItemType

probe_server = FastMCP("probe")


@probe_server.tool
def probe(
    identifier: NoteIdentifier,
    project: ProjectParam = None,
    since: Since = "7d",
    page: Page = 1,
    page_size: PageSize = 10,
    filters: Filters = None,
    items: Items = None,
) -> dict[str, Any]:
    """Echo the validated arguments."""
    return {
        "identifier": identifier,
        "project": project,
        "since": since,
        "page": page,
        "page_size": page_size,
        "filters": filters,
        "items": items,
    }


async def call_probe(**arguments: Any) -> dict[str, Any]:
    async with Client(probe_server) as client:
        result = await client.call_tool("probe", arguments)
        assert result.structured_content is not None
        return result.structured_content


async def probe_error(**arguments: Any) -> str:
    async with Client(probe_server) as client:
        result = await client.call_tool("probe", arguments, raise_on_error=False)
        assert result.is_error
        return result.content[0].text  # pyright: ignore[reportAttributeAccessIssue]


# --- Schema: models see only the primary names ---


@pytest.mark.asyncio
async def test_schema_lists_primary_names_only():
    async with Client(probe_server) as client:
        (tool,) = await client.list_tools()
    assert set(tool.input_schema["properties"]) == {
        "identifier",
        "project",
        "since",
        "page",
        "page_size",
        "filters",
        "items",
    }
    assert tool.input_schema["required"] == ["identifier"]


# --- Hidden aliases ---


@pytest.mark.asyncio
async def test_hidden_aliases_reach_the_primary_parameters():
    echoed = await call_probe(
        file_path="notes/plan.md",
        project_id="research",
        timeframe="2 days ago",
        limit=25,
        metadata_filters={"status": "draft"},
        entity_types=["entity", "observation"],
    )
    assert echoed["identifier"] == "notes/plan.md"
    assert echoed["project"] == "research"
    assert echoed["since"] == "2d"
    assert echoed["page_size"] == 25
    assert echoed["filters"] == {"status": "draft"}
    # "entity" is the API's word for a whole note.
    assert echoed["items"] == ["note", "observation"]


@pytest.mark.asyncio
async def test_null_item_members_are_refused():
    """[null] is not a kind; it must not quietly become the default note search."""
    assert "items" in await probe_error(identifier="plan", items=[None])
    assert "items" in await probe_error(identifier="plan", items="[null]")
    assert "items" in await probe_error(identifier="plan", items='[null, "relation"]')


# --- Coercion of string-serialized values ---


@pytest.mark.asyncio
async def test_string_serialized_filters_and_items_are_parsed():
    echoed = await call_probe(
        identifier="plan",
        filters='{"priority": {"$in": ["high"]}}',
        items="note,relation",
    )
    assert echoed["filters"] == {"priority": {"$in": ["high"]}}
    assert echoed["items"] == ["note", "relation"]


@pytest.mark.asyncio
async def test_explicit_null_items_mean_the_default():
    # Defaults skip validation, so only an explicit null exercises the parser's None path.
    echoed = await call_probe(identifier="plan", items=None)
    assert echoed["items"] is None


# --- Bounds and rejected values ---


@pytest.mark.asyncio
async def test_page_size_above_the_maximum_is_a_tool_error():
    message = await probe_error(identifier="plan", page_size=101)
    assert "page_size" in message


@pytest.mark.asyncio
async def test_page_below_one_is_a_tool_error():
    message = await probe_error(identifier="plan", page=0)
    assert "page" in message


@pytest.mark.asyncio
async def test_unknown_item_kind_is_a_tool_error():
    message = await probe_error(identifier="plan", items=["chapter"])
    assert "items" in message


@pytest.mark.asyncio
async def test_non_list_items_are_rejected_not_crashed():
    message = await probe_error(identifier="plan", items=42)
    assert "items" in message


@pytest.mark.asyncio
async def test_empty_identifier_is_a_tool_error():
    message = await probe_error(identifier="")
    assert "identifier" in message


# --- Project selection ---


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("  ", None),
        ("research", ProjectByName("research")),
        ("team/specs", ProjectByName("team/specs")),
        (
            " 5BB127BB-8273-45F7-87D3-F9EE13CCCEB5 ",
            ProjectById("5bb127bb-8273-45f7-87d3-f9ee13ccceb5"),
        ),
    ],
)
def test_parse_project_classifies_names_and_ids(value, expected):
    assert parse_project(value) == expected


def test_routing_arguments_never_set_both():
    assert routing_arguments(None) == (None, None)
    assert routing_arguments(ProjectByName("research")) == ("research", None)
    assert routing_arguments(ProjectById("5bb127bb-8273-45f7-87d3-f9ee13ccceb5")) == (
        None,
        "5bb127bb-8273-45f7-87d3-f9ee13ccceb5",
    )


# --- Item kinds ---


def test_search_item_types_translates_note_to_entity():
    assert search_item_types(["note", "observation", "relation"]) == [
        SearchItemType.ENTITY,
        SearchItemType.OBSERVATION,
        SearchItemType.RELATION,
    ]
    assert search_item_types(None) is None
    assert search_item_types([]) is None


# --- Refusals ---


def test_refusal_text_names_the_code_and_the_alternative():
    error = refusal("NOT_FOUND", "No note matches 'plan'.", try_instead="bm_search(query='plan')")
    assert isinstance(error, ToolError)
    assert str(error) == "NOT_FOUND: No note matches 'plan'.\nTry instead: bm_search(query='plan')"


def test_refusal_text_without_an_alternative():
    assert str(refusal("UNSUPPORTED", "Images cannot be edited.")) == (
        "UNSUPPORTED: Images cannot be edited."
    )


def test_refusal_json_payload():
    error = refusal(
        "REVISION_CONFLICT",
        "The note changed since you read it.",
        try_instead="bm_read(identifier='plan', include_content=False)",
        output_format="json",
    )
    assert json.loads(str(error)) == {
        "error": "REVISION_CONFLICT",
        "message": "The note changed since you read it.",
        "try_instead": "bm_read(identifier='plan', include_content=False)",
    }


def test_refusal_json_payload_without_an_alternative():
    error = refusal("AMBIGUOUS", "Two notes match.", output_format="json")
    assert json.loads(str(error)) == {"error": "AMBIGUOUS", "message": "Two notes match."}
