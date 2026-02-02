"""Tool contract tests for MCP tool signatures."""

from __future__ import annotations

import inspect

from basic_memory.mcp import tools


EXPECTED_TOOL_SIGNATURES: dict[str, list[str]] = {
    "build_context": ["url", "project", "depth", "timeframe", "page", "page_size", "max_related"],
    "canvas": ["nodes", "edges", "title", "directory", "project"],
    "create_memory_project": ["project_name", "project_path", "set_default"],
    "delete_note": ["identifier", "is_directory", "project"],
    "delete_project": ["project_name"],
    "edit_note": [
        "identifier",
        "operation",
        "content",
        "project",
        "section",
        "find_text",
        "expected_replacements",
    ],
    "fetch": ["id"],
    "list_directory": ["dir_name", "depth", "file_name_glob", "project"],
    "list_memory_projects": [],
    "move_note": ["identifier", "destination_path", "is_directory", "project"],
    "read_content": ["path", "project"],
    "read_note": ["identifier", "project", "page", "page_size"],
    "recent_activity": ["type", "depth", "timeframe", "project"],
    "search": ["query"],
    "search_by_metadata": ["filters", "project", "limit", "offset"],
    "search_notes": [
        "query",
        "project",
        "page",
        "page_size",
        "search_type",
        "types",
        "entity_types",
        "after_date",
        "metadata_filters",
        "tags",
        "status",
    ],
    "view_note": ["identifier", "project", "page", "page_size"],
    "write_note": ["title", "content", "directory", "project", "tags", "note_type"],
}


TOOL_FUNCTIONS: dict[str, object] = {
    "build_context": tools.build_context,
    "canvas": tools.canvas,
    "create_memory_project": tools.create_memory_project,
    "delete_note": tools.delete_note,
    "delete_project": tools.delete_project,
    "edit_note": tools.edit_note,
    "fetch": tools.fetch,
    "list_directory": tools.list_directory,
    "list_memory_projects": tools.list_memory_projects,
    "move_note": tools.move_note,
    "read_content": tools.read_content,
    "read_note": tools.read_note,
    "recent_activity": tools.recent_activity,
    "search": tools.search,
    "search_by_metadata": tools.search_by_metadata,
    "search_notes": tools.search_notes,
    "view_note": tools.view_note,
    "write_note": tools.write_note,
}


def _signature_params(tool_obj: object) -> list[str]:
    fn = tool_obj.fn
    params = []
    for param in inspect.signature(fn).parameters.values():
        if param.name == "context":
            continue
        params.append(param.name)
    return params


def test_mcp_tool_signatures_are_stable():
    assert set(TOOL_FUNCTIONS.keys()) == set(EXPECTED_TOOL_SIGNATURES.keys())

    for tool_name, tool_obj in TOOL_FUNCTIONS.items():
        assert _signature_params(tool_obj) == EXPECTED_TOOL_SIGNATURES[tool_name]
