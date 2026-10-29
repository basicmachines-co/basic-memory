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
    "build_context": [
        "url",
        "project",
        "project_id",
        "depth",
        "timeframe",
        "page",
        "page_size",
        "max_related",
        "output_format",
        "compact",
    ],
    "cat": [
        "identifier",
        "start_line",
        "end_line",
        "section",
        "max_tokens",
        "include_frontmatter",
        "project",
        "project_id",
    ],
    "create_memory_project": [
        "project_name",
        "project_path",
        "set_default",
        "workspace",
        "output_format",
    ],
    "delete_project": ["project_name", "delete_notes", "workspace", "output_format"],
    "edit_note": [
        "identifier",
        "operation",
        "content",
        "project",
        "workspace",
        "project_id",
        "section",
        "find_text",
        "expected_replacements",
        "replace_subsections",
        "metadata",
        "expected_checksum",
        "output_format",
    ],
    "fetch": ["id"],
    "find": [
        "path",
        "name",
        "depth",
        "page",
        "page_size",
        "meta",
        "fields",
        "project",
        "project_id",
    ],
    "grep": [
        "pattern",
        "semantic",
        "page",
        "page_size",
        "project",
        "project_id",
        "context_lines",
        "max_matches",
    ],
    "list_memory_projects": ["output_format"],
    "list_workspaces": ["output_format"],
    "ls": ["path", "page", "page_size", "project", "project_id"],
    "man": ["page", "query", "project", "project_id"],
    "read_note": [
        "identifier",
        "project",
        "project_id",
        "page",
        "page_size",
        "output_format",
        "include_frontmatter",
        "start_line",
        "end_line",
        "include_content",
    ],
    "recent_activity": [
        "type",
        "depth",
        "timeframe",
        "page",
        "page_size",
        "project",
        "project_id",
        "output_format",
    ],
    "schema_diff": ["note_type", "project", "project_id", "output_format"],
    "schema_infer": ["note_type", "threshold", "project", "project_id", "output_format"],
    "schema_validate": ["note_type", "identifier", "project", "project_id", "output_format"],
    "search": ["query"],
    "search_notes": [
        "query",
        "project",
        "project_id",
        "search_all_projects",
        "projects",
        "page",
        "page_size",
        "search_type",
        "output_format",
        "note_types",
        "entity_types",
        "categories",
        "after_date",
        "metadata_filters",
        "tags",
        "status",
        "min_similarity",
        "valid_at",
        "valid_overlaps",
        "time_kind",
        "compact",
    ],
    "tail": ["timeframe", "lines", "project", "project_id"],
    "write_note": [
        "title",
        "content",
        "directory",
        "project",
        "workspace",
        "project_id",
        "tags",
        "note_type",
        "metadata",
        "overwrite",
        "expected_checksum",
        "output_format",
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
    "build_context": {"readOnlyHint": True, "destructiveHint": False},
    "fetch": {"readOnlyHint": True, "destructiveHint": False},
    "list_memory_projects": {"readOnlyHint": True, "destructiveHint": False},
    "list_workspaces": {"readOnlyHint": True, "destructiveHint": False},
    "read_note": {"readOnlyHint": True, "destructiveHint": False},
    "recent_activity": {"readOnlyHint": True, "destructiveHint": False},
    "schema_diff": {"readOnlyHint": True, "destructiveHint": False},
    "schema_infer": {"readOnlyHint": True, "destructiveHint": False},
    "schema_validate": {"readOnlyHint": True, "destructiveHint": False},
    "search": {"readOnlyHint": True, "destructiveHint": False},
    "search_notes": {"readOnlyHint": True, "destructiveHint": False},
    # create_memory_project is purely additive: it creates a new project and errors
    # if the target already exists.
    "create_memory_project": {"readOnlyHint": False, "destructiveHint": False},
    # edit_note's find_replace/replace_section overwrite existing content, so it is
    # destructive even though append/prepend are additive.
    "edit_note": {"readOnlyHint": False, "destructiveHint": True},
    "write_note": {"readOnlyHint": False, "destructiveHint": True},
}

# The MCP-UI tools are disabled in tools/__init__.py but register onto the shared
# server whenever tests import their module directly, so tolerate their presence
# without requiring it — keeps this contract independent of test execution order.
# The POSIX tools are enabled by default during lifespan startup, but remain
# hidden before config is read. Optional status keeps this direct-list contract
# independent of whether another test has started the shared server.
OPTIONAL_TOOL_ANNOTATIONS: dict[str, dict[str, bool]] = {
    "read_note_ui": {"readOnlyHint": True, "destructiveHint": False},
    "search_notes_ui": {"readOnlyHint": True, "destructiveHint": False},
    "cat": {"readOnlyHint": True, "destructiveHint": False},
    "find": {"readOnlyHint": True, "destructiveHint": False},
    "grep": {"readOnlyHint": True, "destructiveHint": False},
    "ls": {"readOnlyHint": True, "destructiveHint": False},
    "man": {"readOnlyHint": True, "destructiveHint": False},
    "tail": {"readOnlyHint": True, "destructiveHint": False},
    # Listed only when enable_project_delete is on.
    "bm_project_delete": {"readOnlyHint": False, "destructiveHint": True},
    "delete_project": {"readOnlyHint": False, "destructiveHint": True},
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
    "build_context": tools.build_context,
    "cat": tools.cat,
    "create_memory_project": tools.create_memory_project,
    "delete_project": tools.delete_project,
    "edit_note": tools.edit_note,
    "fetch": tools.fetch,
    "find": tools.find,
    "grep": tools.grep,
    "list_memory_projects": tools.list_memory_projects,
    "list_workspaces": tools.list_workspaces,
    "ls": tools.ls,
    "man": tools.man,
    "read_note": tools.read_note,
    "recent_activity": tools.recent_activity,
    "schema_diff": tools.schema_diff,
    "schema_infer": tools.schema_infer,
    "schema_validate": tools.schema_validate,
    "search": tools.search,
    "search_notes": tools.search_notes,
    "tail": tools.tail,
    "write_note": tools.write_note,
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
