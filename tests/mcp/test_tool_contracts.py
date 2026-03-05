"""Tool contract tests for MCP tool signatures."""

from __future__ import annotations

import inspect

from basic_memory.mcp import tools


EXPECTED_TOOL_SIGNATURES: dict[str, list[str]] = {
    "build_context": [
        "url",
        "project",
        "workspace",
        "depth",
        "timeframe",
        "page",
        "page_size",
        "max_related",
        "output_format",
    ],
    "canvas": ["nodes", "edges", "title", "directory", "project", "workspace"],
    "cloud_info": [],
    "create_memory_project": ["project_name", "project_path", "set_default", "output_format"],
    "delete_note": ["identifier", "is_directory", "project", "workspace", "output_format"],
    "delete_project": ["project_name"],
    "edit_note": [
        "identifier",
        "operation",
        "content",
        "project",
        "workspace",
        "section",
        "find_text",
        "expected_replacements",
        "output_format",
    ],
    "fcm_export_model": ["format", "selection", "project", "workspace", "output_format"],
    "fcm_import_model": ["source", "format", "merge_mode", "project", "workspace", "output_format"],
    "fcm_rank_actions": ["goal", "constraints", "top_k", "project", "workspace", "output_format"],
    "fcm_simulate": ["actions", "scenario", "clamp_rules", "project", "workspace", "output_format"],
    "fetch": ["id"],
    "graph_health": ["scope", "timeframe", "project", "workspace", "output_format"],
    "graph_impact": [
        "target",
        "horizon",
        "relation_filters",
        "include_reasons",
        "project",
        "workspace",
        "output_format",
    ],
    "graph_lineage": [
        "start",
        "goal",
        "max_hops",
        "relation_filters",
        "project",
        "workspace",
        "output_format",
    ],
    "graph_reindex": ["mode", "reason", "project", "workspace", "output_format"],
    "list_directory": ["dir_name", "depth", "file_name_glob", "project", "workspace"],
    "list_memory_projects": ["output_format", "workspace"],
    "list_workspaces": ["output_format"],
    "move_note": [
        "identifier",
        "destination_path",
        "destination_folder",
        "is_directory",
        "project",
        "workspace",
        "output_format",
    ],
    "read_content": ["path", "project", "workspace"],
    "read_note": [
        "identifier",
        "project",
        "workspace",
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
        "workspace",
        "output_format",
    ],
    "schema_diff": ["note_type", "project", "workspace", "output_format"],
    "schema_infer": ["note_type", "threshold", "project", "workspace", "output_format"],
    "schema_validate": ["note_type", "identifier", "project", "workspace", "output_format"],
    "search": ["query"],
    "search_notes": [
        "query",
        "project",
        "workspace",
        "page",
        "page_size",
        "search_type",
        "output_format",
        "note_types",
        "entity_types",
        "after_date",
        "metadata_filters",
        "tags",
        "status",
        "min_similarity",
    ],
    "view_note": ["identifier", "project", "workspace", "page", "page_size"],
    "write_note": [
        "title",
        "content",
        "directory",
        "project",
        "workspace",
        "tags",
        "note_type",
        "metadata",
        "overwrite",
        "output_format",
    ],
}


TOOL_FUNCTIONS: dict[str, object] = {
    "build_context": tools.build_context,
    "canvas": tools.canvas,
    "cloud_info": tools.cloud_info,
    "create_memory_project": tools.create_memory_project,
    "delete_note": tools.delete_note,
    "delete_project": tools.delete_project,
    "edit_note": tools.edit_note,
    "fcm_export_model": tools.fcm_export_model,
    "fcm_import_model": tools.fcm_import_model,
    "fcm_rank_actions": tools.fcm_rank_actions,
    "fcm_simulate": tools.fcm_simulate,
    "fetch": tools.fetch,
    "graph_health": tools.graph_health,
    "graph_impact": tools.graph_impact,
    "graph_lineage": tools.graph_lineage,
    "graph_reindex": tools.graph_reindex,
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
    for param in inspect.signature(tool_obj).parameters.values():
        if param.name == "context":
            continue
        params.append(param.name)
    return params


def test_mcp_tool_signatures_are_stable():
    assert set(TOOL_FUNCTIONS.keys()) == set(EXPECTED_TOOL_SIGNATURES.keys())

    for tool_name, tool_obj in TOOL_FUNCTIONS.items():
        assert _signature_params(tool_obj) == EXPECTED_TOOL_SIGNATURES[tool_name]
