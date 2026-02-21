"""Tests for CLI tool commands.

All commands return JSON via MCP tool output_format="json".
Tests mock the MCP tool functions directly.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()

# --- Shared mock data ---

WRITE_NOTE_RESULT = {
    "title": "Test Note",
    "permalink": "notes/test-note",
    "file_path": "notes/Test Note.md",
    "checksum": "abc123",
    "action": "created",
}

READ_NOTE_RESULT = {
    "title": "Test Note",
    "permalink": "notes/test-note",
    "file_path": "notes/Test Note.md",
    "content": "# Test Note\n\nhello world",
    "frontmatter": {"title": "Test Note", "tags": ["test"]},
}

EDIT_NOTE_RESULT = {
    "title": "Test Note",
    "permalink": "notes/test-note",
    "file_path": "notes/Test Note.md",
    "checksum": "def456",
    "operation": "append",
}

BUILD_CONTEXT_RESULT = {
    "results": [],
    "metadata": {"uri": "test/topic", "depth": 1},
    "page": 1,
    "page_size": 10,
}

RECENT_ACTIVITY_RESULT = [
    {
        "type": "entity",
        "title": "Note A",
        "permalink": "notes/note-a",
        "file_path": "notes/Note A.md",
        "created_at": "2025-01-01 00:00:00",
    },
    {
        "type": "entity",
        "title": "Note B",
        "permalink": "notes/note-b",
        "file_path": "notes/Note B.md",
        "created_at": "2025-01-02 00:00:00",
    },
]

SEARCH_RESULT = {
    "query": "test",
    "total": 1,
    "page": 1,
    "page_size": 10,
    "results": [
        {
            "type": "entity",
            "title": "Test Note",
            "permalink": "notes/test-note",
            "file_path": "notes/Test Note.md",
        }
    ],
}


def _mock_config_manager():
    """Create a mock ConfigManager that avoids reading real config."""
    mock_cm = MagicMock()
    mock_cm.config = MagicMock()
    mock_cm.default_project = "test-project"
    mock_cm.get_project.return_value = ("test-project", "/tmp/test")
    return mock_cm


# --- write-note ---


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_write_note",
    new_callable=AsyncMock,
    return_value=WRITE_NOTE_RESULT,
)
def test_write_note_json_output(mock_mcp_write, mock_config_cls):
    """write-note outputs valid JSON from MCP tool."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Test Note",
            "--folder",
            "notes",
            "--content",
            "hello world",
        ],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["title"] == "Test Note"
    assert data["permalink"] == "notes/test-note"
    assert data["file_path"] == "notes/Test Note.md"
    mock_mcp_write.assert_called_once()
    # Verify output_format="json" was passed
    assert mock_mcp_write.call_args.kwargs["output_format"] == "json"


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_write_note",
    new_callable=AsyncMock,
    return_value=WRITE_NOTE_RESULT,
)
def test_write_note_with_tags(mock_mcp_write, mock_config_cls):
    """write-note passes tags through to MCP tool."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Test Note",
            "--folder",
            "notes",
            "--content",
            "hello",
            "--tags",
            "python",
            "--tags",
            "async",
        ],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp_write.call_args.kwargs["tags"] == ["python", "async"]


# --- read-note ---


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT,
)
def test_read_note_json_output(mock_mcp_read, mock_config_cls):
    """read-note outputs valid JSON from MCP tool."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "read-note", "test-note"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["title"] == "Test Note"
    assert data["permalink"] == "notes/test-note"
    assert data["content"] == "# Test Note\n\nhello world"
    assert data["frontmatter"] == {"title": "Test Note", "tags": ["test"]}
    mock_mcp_read.assert_called_once()
    assert mock_mcp_read.call_args.kwargs["output_format"] == "json"


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT,
)
def test_read_note_workspace_passthrough(mock_mcp_read, mock_config_cls):
    """read-note --workspace passes workspace through to the MCP tool call."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "read-note", "test-note", "--workspace", "tenant-123"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp_read.call_args.kwargs["workspace"] == "tenant-123"


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT,
)
def test_read_note_include_frontmatter(mock_mcp_read, mock_config_cls):
    """read-note --include-frontmatter passes flag through to MCP tool."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "read-note", "test-note", "--include-frontmatter"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp_read.call_args.kwargs["include_frontmatter"] is True


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT,
)
def test_read_note_pagination(mock_mcp_read, mock_config_cls):
    """read-note --page and --page-size are passed through."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "read-note", "test-note", "--page", "2", "--page-size", "5"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp_read.call_args.kwargs["page"] == 2
    assert mock_mcp_read.call_args.kwargs["page_size"] == 5


# --- edit-note ---


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_edit_note",
    new_callable=AsyncMock,
    return_value=EDIT_NOTE_RESULT,
)
def test_edit_note_json_output(mock_mcp_edit, mock_config_cls):
    """edit-note outputs valid JSON from MCP tool."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            "test-note",
            "--operation",
            "append",
            "--content",
            "new content",
        ],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["title"] == "Test Note"
    assert data["operation"] == "append"
    mock_mcp_edit.assert_called_once()
    assert mock_mcp_edit.call_args.kwargs["output_format"] == "json"


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_edit_note",
    new_callable=AsyncMock,
    return_value={"title": "Test", "permalink": "test", "error": "Edit failed: not found"},
)
def test_edit_note_error_response(mock_mcp_edit, mock_config_cls):
    """edit-note exits with code 1 when MCP tool returns error field."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            "test-note",
            "--operation",
            "append",
            "--content",
            "content",
        ],
    )

    assert result.exit_code == 1


# --- build-context ---


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_build_context",
    new_callable=AsyncMock,
    return_value=BUILD_CONTEXT_RESULT,
)
def test_build_context_json_output(mock_build_ctx, mock_config_cls):
    """build-context outputs valid JSON from MCP tool."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "build-context", "memory://test/topic"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert "results" in data
    mock_build_ctx.assert_called_once()
    assert mock_build_ctx.call_args.kwargs["output_format"] == "json"


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_build_context",
    new_callable=AsyncMock,
    return_value=BUILD_CONTEXT_RESULT,
)
def test_build_context_with_options(mock_build_ctx, mock_config_cls):
    """build-context passes depth, timeframe, pagination through."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "build-context",
            "memory://test/topic",
            "--depth",
            "2",
            "--timeframe",
            "30d",
            "--page",
            "3",
            "--max-related",
            "5",
        ],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    kwargs = mock_build_ctx.call_args.kwargs
    assert kwargs["depth"] == 2
    assert kwargs["timeframe"] == "30d"
    assert kwargs["page"] == 3
    assert kwargs["max_related"] == 5


# --- recent-activity ---


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_recent_activity",
    new_callable=AsyncMock,
    return_value=RECENT_ACTIVITY_RESULT,
)
def test_recent_activity_json_output(mock_mcp_recent, mock_config_cls):
    """recent-activity outputs valid JSON list from MCP tool."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "recent-activity"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["title"] == "Note A"
    assert data[1]["title"] == "Note B"
    mock_mcp_recent.assert_called_once()
    assert mock_mcp_recent.call_args.kwargs["output_format"] == "json"


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_recent_activity",
    new_callable=AsyncMock,
    return_value=RECENT_ACTIVITY_RESULT,
)
def test_recent_activity_pagination(mock_mcp_recent, mock_config_cls):
    """recent-activity passes --page and --page-size through."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "recent-activity", "--page", "2", "--page-size", "10"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    kwargs = mock_mcp_recent.call_args.kwargs
    assert kwargs["page"] == 2
    assert kwargs["page_size"] == 10


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_recent_activity",
    new_callable=AsyncMock,
    return_value=[],
)
def test_recent_activity_empty(mock_mcp_recent, mock_config_cls):
    """recent-activity handles empty results."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "recent-activity"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data == []


# --- search-notes ---


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_search",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT,
)
def test_search_notes_json_output(mock_mcp_search, mock_config_cls):
    """search-notes outputs valid JSON from MCP tool."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "search-notes", "test query"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["total"] == 1
    assert data["results"][0]["title"] == "Test Note"
    mock_mcp_search.assert_called_once()
    assert mock_mcp_search.call_args.kwargs["output_format"] == "json"


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_search",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT,
)
def test_search_notes_with_meta_filter(mock_mcp_search, mock_config_cls):
    """search-notes --meta key=value builds metadata filters."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "search-notes", "query", "--meta", "status=draft"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp_search.call_args.kwargs["metadata_filters"] == {"status": "draft"}


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_search",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT,
)
def test_search_notes_permalink_mode(mock_mcp_search, mock_config_cls):
    """search-notes --permalink sets search_type."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "search-notes", "specs/*", "--permalink"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp_search.call_args.kwargs["search_type"] == "permalink"


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_search",
    new_callable=AsyncMock,
    return_value="Error: search failed",
)
def test_search_notes_string_error(mock_mcp_search, mock_config_cls):
    """search-notes exits with code 1 when MCP returns string error."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "search-notes", "query"],
    )

    assert result.exit_code == 1


# --- Project resolution ---


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_write_note",
    new_callable=AsyncMock,
    return_value=WRITE_NOTE_RESULT,
)
def test_project_not_found_error(mock_mcp_write, mock_config_cls):
    """Commands exit with error when specified project doesn't exist."""
    mock_cm = _mock_config_manager()
    mock_cm.get_project.return_value = (None, None)
    mock_config_cls.return_value = mock_cm

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Test",
            "--folder",
            "notes",
            "--content",
            "hello",
            "--project",
            "nonexistent",
        ],
    )

    assert result.exit_code == 1
    assert "No project found" in result.output


# --- Routing flags ---


def test_routing_both_flags_error():
    """Commands exit with error when both --local and --cloud are specified."""
    result = runner.invoke(
        cli_app,
        [
            "tool",
            "recent-activity",
            "--local",
            "--cloud",
        ],
    )

    assert result.exit_code == 1


# --- schema-validate ---

SCHEMA_VALIDATE_RESULT = {
    "entity_type": "person",
    "total_notes": 2,
    "total_entities": 2,
    "valid_count": 1,
    "warning_count": 1,
    "error_count": 1,
    "results": [
        {
            "note_identifier": "people/alice",
            "schema_entity": "person",
            "passed": True,
            "warnings": [],
            "errors": [],
        },
        {
            "note_identifier": "people/bob",
            "schema_entity": "person",
            "passed": False,
            "warnings": ["Missing optional field: role"],
            "errors": ["Missing required field: name"],
        },
    ],
}


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_schema_validate",
    new_callable=AsyncMock,
    return_value=SCHEMA_VALIDATE_RESULT,
)
def test_schema_validate_json_output(mock_mcp, mock_config_cls):
    """schema-validate outputs valid JSON from MCP tool."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "schema-validate", "person"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["entity_type"] == "person"
    assert data["total_notes"] == 2
    mock_mcp.assert_called_once()
    assert mock_mcp.call_args.kwargs["output_format"] == "json"


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_schema_validate",
    new_callable=AsyncMock,
    return_value=SCHEMA_VALIDATE_RESULT,
)
def test_schema_validate_identifier_heuristic(mock_mcp, mock_config_cls):
    """schema-validate treats target with / as identifier, not note_type."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "schema-validate", "people/alice.md"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp.call_args.kwargs["identifier"] == "people/alice.md"
    assert mock_mcp.call_args.kwargs["note_type"] is None


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_schema_validate",
    new_callable=AsyncMock,
    return_value={"error": "No notes found of type 'person'"},
)
def test_schema_validate_error_response(mock_mcp, mock_config_cls):
    """schema-validate outputs error JSON when MCP returns error dict."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "schema-validate", "person"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert "error" in data


# --- schema-infer ---

SCHEMA_INFER_RESULT = {
    "entity_type": "person",
    "notes_analyzed": 5,
    "field_frequencies": [
        {"name": "name", "source": "observation", "count": 5, "total": 5, "percentage": 1.0},
        {"name": "role", "source": "observation", "count": 3, "total": 5, "percentage": 0.6},
    ],
    "suggested_schema": {"name": "string, full name", "role?": "string, job title"},
    "suggested_required": ["name"],
    "suggested_optional": ["role"],
    "excluded": [],
}


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_schema_infer",
    new_callable=AsyncMock,
    return_value=SCHEMA_INFER_RESULT,
)
def test_schema_infer_json_output(mock_mcp, mock_config_cls):
    """schema-infer outputs valid JSON from MCP tool."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "schema-infer", "person"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["entity_type"] == "person"
    assert data["notes_analyzed"] == 5
    mock_mcp.assert_called_once()
    assert mock_mcp.call_args.kwargs["output_format"] == "json"


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_schema_infer",
    new_callable=AsyncMock,
    return_value=SCHEMA_INFER_RESULT,
)
def test_schema_infer_threshold_passthrough(mock_mcp, mock_config_cls):
    """schema-infer passes --threshold through to MCP tool."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "schema-infer", "person", "--threshold", "0.5"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp.call_args.kwargs["threshold"] == 0.5


# --- schema-diff ---

SCHEMA_DIFF_RESULT = {
    "entity_type": "person",
    "schema_found": True,
    "new_fields": [
        {"name": "email", "source": "observation", "count": 3, "total": 5, "percentage": 0.6}
    ],
    "dropped_fields": [],
    "cardinality_changes": [],
}


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_schema_diff",
    new_callable=AsyncMock,
    return_value=SCHEMA_DIFF_RESULT,
)
def test_schema_diff_json_output(mock_mcp, mock_config_cls):
    """schema-diff outputs valid JSON from MCP tool."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "schema-diff", "person"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["entity_type"] == "person"
    assert data["schema_found"] is True
    assert len(data["new_fields"]) == 1
    mock_mcp.assert_called_once()
    assert mock_mcp.call_args.kwargs["output_format"] == "json"
