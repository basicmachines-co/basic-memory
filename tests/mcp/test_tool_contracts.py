"""Tool contract tests for MCP tool signatures."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, cast

import pytest

from basic_memory.mcp import tools
from basic_memory.mcp.server import mcp


EXPECTED_TOOL_SIGNATURES: dict[str, list[str]] = {
    "bm_context": [
        "identifier",
        "depth",
        "since",
        "max_related",
        "compact",
        "page",
        "page_size",
        "output_format",
        "project",
    ],
    "bm_find": [
        "path",
        "name",
        "filters",
        "fields",
        "depth",
        "page",
        "page_size",
        "output_format",
        "project",
    ],
    "bm_list": ["path", "glob", "sort", "page", "page_size", "output_format", "project"],
    "bm_read": [
        "identifier",
        "section",
        "start_line",
        "end_line",
        "max_tokens",
        "include_frontmatter",
        "include_content",
        "output_format",
        "project",
    ],
    "bm_edit": [
        "identifier",
        "command",
        "content",
        "section",
        "find_text",
        "expected_replacements",
        "replace_subsections",
        "metadata",
        "expected_checksum",
        "output_format",
        "project",
    ],
    "bm_move": [
        "destination",
        "identifier",
        "path",
        "expected_checksum",
        "output_format",
        "project",
    ],
    "bm_delete": [
        "identifier",
        "path",
        "recursive",
        "expected_checksum",
        "output_format",
        "project",
    ],
    "bm_project": [
        "command",
        "name",
        "path",
        "set_default",
        "workspace",
        "since",
        "items",
        "page",
        "page_size",
        "output_format",
        "project",
    ],
    "bm_project_delete": ["name", "delete_notes", "workspace", "output_format"],
    "bm_man": ["page", "query", "output_format"],
    "bm_schema": [
        "command",
        "note_type",
        "identifier",
        "threshold",
        "output_format",
        "project",
    ],
    "bm_write": [
        "content",
        "identifier",
        "title",
        "directory",
        "note_type",
        "tags",
        "metadata",
        "overwrite",
        "expected_checksum",
        "output_format",
        "project",
    ],
    "bm_search": [
        "query",
        "mode",
        "filters",
        "note_types",
        "items",
        "categories",
        "since",
        "lines",
        "max_matches",
        "identifier",
        "path",
        "compact",
        "valid_at",
        "valid_overlaps",
        "time_kind",
        "min_similarity",
        "projects",
        "all_projects",
        "page",
        "page_size",
        "output_format",
        "project",
    ],
}


# Directory review requirements differ by client, so keep the stricter shared contract:
# every tool must set annotations.title and explicit readOnlyHint, destructiveHint, and
# openWorldHint values.
EXPECTED_TOOL_ANNOTATIONS: dict[str, dict[str, bool]] = {
    "bm_context": {"readOnlyHint": True, "destructiveHint": False},
    "bm_find": {"readOnlyHint": True, "destructiveHint": False},
    "bm_list": {"readOnlyHint": True, "destructiveHint": False},
    "bm_read": {"readOnlyHint": True, "destructiveHint": False},
    "bm_search": {"readOnlyHint": True, "destructiveHint": False},
    "bm_write": {"readOnlyHint": False, "destructiveHint": True},
    "bm_edit": {"readOnlyHint": False, "destructiveHint": True},
    "bm_move": {"readOnlyHint": False, "destructiveHint": False},
    "bm_delete": {"readOnlyHint": False, "destructiveHint": True},
    "bm_project": {"readOnlyHint": False, "destructiveHint": False},
    "bm_schema": {"readOnlyHint": True, "destructiveHint": False},
    "bm_man": {"readOnlyHint": True, "destructiveHint": False},
}

OPTIONAL_TOOL_ANNOTATIONS: dict[str, dict[str, bool]] = {
    # Listed only when enable_project_delete is on.
    "bm_project_delete": {"readOnlyHint": False, "destructiveHint": True},
}

EXPECTED_EDIT_NOTE_OPERATIONS = [
    "append",
    "prepend",
    "find_replace",
    "replace_section",
    "insert_before_section",
    "insert_after_section",
    "set_metadata",
]


TOOL_FUNCTIONS: dict[str, object] = {
    "bm_context": tools.bm_context,
    "bm_find": tools.bm_find,
    "bm_list": tools.bm_list,
    "bm_read": tools.bm_read,
    "bm_search": tools.bm_search,
    "bm_write": tools.bm_write,
    "bm_edit": tools.bm_edit,
    "bm_move": tools.bm_move,
    "bm_delete": tools.bm_delete,
    "bm_project": tools.bm_project,
    "bm_project_delete": tools.bm_project_delete,
    "bm_schema": tools.bm_schema,
    "bm_man": tools.bm_man,
}


def _signature_params(tool_obj: object) -> list[str]:
    params = []
    for param in inspect.signature(cast(Callable[..., Any], tool_obj)).parameters.values():
        if param.name == "context":
            continue
        params.append(param.name)
    return params


def test_mcp_tool_signatures_are_stable():
    assert set(TOOL_FUNCTIONS.keys()) == set(EXPECTED_TOOL_SIGNATURES.keys())

    for tool_name, tool_obj in TOOL_FUNCTIONS.items():
        assert _signature_params(tool_obj) == EXPECTED_TOOL_SIGNATURES[tool_name]


@pytest.mark.asyncio
async def test_mcp_tool_annotations_meet_directory_requirements():
    """Every tool's wire-level ToolAnnotations must satisfy app directory review.

    Directory validators read ToolAnnotations (not FastMCP's top-level title), so
    each tool needs annotations.title plus explicit readOnlyHint, destructiveHint,
    and openWorldHint values. openWorldHint is False across the board because every
    tool operates on the user's own knowledge base.
    """
    tool_list = await mcp.list_tools()
    tools_by_name = {tool.name: tool for tool in tool_list}

    required = {name for name in EXPECTED_TOOL_ANNOTATIONS if name.startswith("bm_")}
    optional = {"bm_project_delete"}
    registered = set(tools_by_name)
    missing = required - registered
    unexpected = registered - required - optional
    assert not missing, f"Tools missing from server: {sorted(missing)}"
    assert not unexpected, (
        f"Tools without annotation expectations: {sorted(unexpected)} — "
        "add them to EXPECTED_TOOL_ANNOTATIONS with directory-compliant annotations"
    )

    all_expected = {**EXPECTED_TOOL_ANNOTATIONS, **OPTIONAL_TOOL_ANNOTATIONS}
    for tool_name, tool in tools_by_name.items():
        expected = all_expected[tool_name]
        # Assert on the protocol-level payload the directory review actually sees.
        annotations = tool.to_mcp_tool().annotations
        assert annotations is not None, f"Tool '{tool_name}' has no annotations"
        assert annotations.title == tool.title, f"Tool '{tool_name}' has inconsistent titles"
        assert annotations.read_only_hint is expected["readOnlyHint"], (
            f"Tool '{tool_name}' readOnlyHint should be {expected['readOnlyHint']}"
        )
        assert annotations.destructive_hint is expected["destructiveHint"], (
            f"Tool '{tool_name}' destructiveHint should be {expected['destructiveHint']}"
        )
        assert annotations.open_world_hint is False, (
            f"Tool '{tool_name}' openWorldHint should be False"
        )


@pytest.mark.asyncio
async def test_edit_note_operation_schema_exposes_supported_operations():
    """The edit operation is a fixed choice set, not a free-form string."""
    tool_list = await mcp.list_tools()
    edit_note_tool = next(tool for tool in tool_list if tool.name == "bm_edit")

    input_schema = edit_note_tool.to_mcp_tool().input_schema
    operation_schema = input_schema["properties"]["command"]

    assert operation_schema["type"] == "string"
    assert operation_schema["enum"] == EXPECTED_EDIT_NOTE_OPERATIONS


@pytest.mark.asyncio
async def test_list_directory_sort_schema_exposes_supported_orders():
    """Directory sorting is a closed MCP choice set, not a free-form string."""
    tool_list = await mcp.list_tools()
    list_directory_tool = next(tool for tool in tool_list if tool.name == "bm_list")

    input_schema = list_directory_tool.to_mcp_tool().input_schema
    sort_schema = input_schema["properties"]["sort"]

    assert sort_schema["anyOf"][0] == {
        "enum": ["title_asc", "title_desc", "updated_asc", "updated_desc"],
        "type": "string",
    }


@pytest.mark.asyncio
async def test_mcp_tools_have_title_and_tags():
    """Every registered MCP tool must declare a human-readable title and at least one tag.

    This guards against regressions where a new tool is added without the Phase 1
    FastMCP metadata (title + tags) required by issue #826.
    """
    tool_list = await mcp.list_tools()
    for tool in tool_list:
        assert tool.title, f"Tool '{tool.name}' is missing a 'title' annotation"
        assert tool.tags, f"Tool '{tool.name}' is missing 'tags' annotation"
