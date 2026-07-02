"""Tool contract tests for MCP tool signatures."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, cast

import pytest

from basic_memory.mcp import tools
from basic_memory.mcp.server import mcp


EXPECTED_TOOL_SIGNATURES: dict[str, list[str]] = {
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
    ],
    "canvas": ["nodes", "edges", "title", "directory", "project", "project_id"],
    "cloud_info": [],
    "create_memory_project": [
        "project_name",
        "project_path",
        "set_default",
        "workspace",
        "output_format",
    ],
    "delete_note": ["identifier", "is_directory", "project", "project_id", "output_format"],
    "delete_project": ["project_name", "workspace"],
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
        "output_format",
    ],
    "fetch": ["id"],
    "list_directory": ["dir_name", "depth", "file_name_glob", "project", "project_id"],
    "list_memory_projects": ["output_format"],
    "list_workspaces": ["output_format"],
    "move_note": [
        "identifier",
        "destination_path",
        "destination_folder",
        "is_directory",
        "project",
        "project_id",
        "output_format",
    ],
    "read_content": ["path", "project", "project_id"],
    "read_note": [
        "identifier",
        "project",
        "project_id",
        "page",
        "page_size",
        "output_format",
        "include_frontmatter",
    ],
    "release_notes": [],
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
    ],
    "view_note": ["identifier", "project", "project_id"],
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
        "output_format",
    ],
}


# Anthropic directory review requirements (connectors/building/review-criteria):
# every tool must set annotations.title, read-only tools must set readOnlyHint=True,
# and write tools must set an explicit readOnlyHint=False plus a destructiveHint.
EXPECTED_TOOL_ANNOTATIONS: dict[str, dict[str, bool]] = {
    "build_context": {"readOnlyHint": True},
    "cloud_info": {"readOnlyHint": True},
    "fetch": {"readOnlyHint": True},
    "list_directory": {"readOnlyHint": True},
    "list_memory_projects": {"readOnlyHint": True},
    "list_workspaces": {"readOnlyHint": True},
    "read_content": {"readOnlyHint": True},
    "read_note": {"readOnlyHint": True},
    "recent_activity": {"readOnlyHint": True},
    "release_notes": {"readOnlyHint": True},
    "schema_diff": {"readOnlyHint": True},
    "schema_infer": {"readOnlyHint": True},
    "schema_validate": {"readOnlyHint": True},
    "search": {"readOnlyHint": True},
    "search_notes": {"readOnlyHint": True},
    "view_note": {"readOnlyHint": True},
    # canvas falls back to PUT when the file already exists, replacing its content.
    "canvas": {"readOnlyHint": False, "destructiveHint": True},
    # create_memory_project is purely additive: it creates a new project and errors
    # if the target already exists.
    "create_memory_project": {"readOnlyHint": False, "destructiveHint": False},
    "delete_note": {"readOnlyHint": False, "destructiveHint": True},
    "delete_project": {"readOnlyHint": False, "destructiveHint": True},
    # edit_note's find_replace/replace_section overwrite existing content, so it is
    # destructive even though append/prepend are additive.
    "edit_note": {"readOnlyHint": False, "destructiveHint": True},
    # move_note refuses to overwrite an existing destination and preserves all
    # content — it relocates and propagates links, so no data can be lost. Keeping
    # it non-destructive lets clients allowlist bulk lifecycle moves.
    "move_note": {"readOnlyHint": False, "destructiveHint": False},
    "write_note": {"readOnlyHint": False, "destructiveHint": True},
}

# The MCP-UI tools are disabled in tools/__init__.py but register onto the shared
# server whenever tests import their module directly, so tolerate their presence
# without requiring it — keeps this contract independent of test execution order.
OPTIONAL_TOOL_ANNOTATIONS: dict[str, dict[str, bool]] = {
    "read_note_ui": {"readOnlyHint": True},
    "search_notes_ui": {"readOnlyHint": True},
}


TOOL_FUNCTIONS: dict[str, object] = {
    "build_context": tools.build_context,
    "canvas": tools.canvas,
    "cloud_info": tools.cloud_info,
    "create_memory_project": tools.create_memory_project,
    "delete_note": tools.delete_note,
    "delete_project": tools.delete_project,
    "edit_note": tools.edit_note,
    "fetch": tools.fetch,
    "list_directory": tools.list_directory,
    "list_memory_projects": tools.list_memory_projects,
    "list_workspaces": tools.list_workspaces,
    "move_note": tools.move_note,
    "read_content": tools.read_content,
    "read_note": tools.read_note,
    "release_notes": tools.release_notes,
    "recent_activity": tools.recent_activity,
    "schema_diff": tools.schema_diff,
    "schema_infer": tools.schema_infer,
    "schema_validate": tools.schema_validate,
    "search": tools.search,
    "search_notes": tools.search_notes,
    "view_note": tools.view_note,
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
    """Every tool's wire-level ToolAnnotations must satisfy Anthropic directory review.

    The directory validator reads ToolAnnotations (not FastMCP's top-level title), so
    each tool needs annotations.title, an explicit readOnlyHint, and — for write
    tools — a destructiveHint. openWorldHint is False across the board because every
    tool operates on the user's own knowledge base.
    """
    tool_list = await mcp.list_tools()
    tools_by_name = {tool.name: tool for tool in tool_list}

    required = set(EXPECTED_TOOL_ANNOTATIONS)
    optional = set(OPTIONAL_TOOL_ANNOTATIONS)
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
        assert annotations.title, f"Tool '{tool_name}' is missing annotations.title"
        assert annotations.readOnlyHint is expected["readOnlyHint"], (
            f"Tool '{tool_name}' readOnlyHint should be {expected['readOnlyHint']}"
        )
        if expected["readOnlyHint"] is False:
            assert annotations.destructiveHint is expected["destructiveHint"], (
                f"Tool '{tool_name}' destructiveHint should be {expected['destructiveHint']}"
            )
        assert annotations.openWorldHint is False, (
            f"Tool '{tool_name}' openWorldHint should be False"
        )


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
