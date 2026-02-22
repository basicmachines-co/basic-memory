"""Tests for CLI schema commands (Rich output).

Tests mock the MCP tool functions and verify Rich-formatted output.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


# --- Shared mock data ---

VALIDATE_REPORT = {
    "note_type": "person",
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

INFER_REPORT = {
    "note_type": "person",
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

DIFF_REPORT_WITH_DRIFT = {
    "note_type": "person",
    "schema_found": True,
    "new_fields": [
        {"name": "email", "source": "observation", "count": 3, "total": 5, "percentage": 0.6}
    ],
    "dropped_fields": [
        {"name": "phone", "source": "observation", "count": 0, "total": 5, "percentage": 0.0}
    ],
    "cardinality_changes": ["role: single -> array"],
}

DIFF_REPORT_NO_DRIFT = {
    "note_type": "person",
    "schema_found": True,
    "new_fields": [],
    "dropped_fields": [],
    "cardinality_changes": [],
}


def _mock_config_manager():
    """Create a mock ConfigManager that avoids reading real config."""
    mock_cm = MagicMock()
    mock_cm.config = MagicMock()
    mock_cm.default_project = "test-project"
    mock_cm.get_project.return_value = ("test-project", "/tmp/test")
    return mock_cm


# --- validate ---


@patch("basic_memory.cli.commands.schema.ConfigManager")
@patch(
    "basic_memory.cli.commands.schema.mcp_schema_validate",
    new_callable=AsyncMock,
    return_value=VALIDATE_REPORT,
)
def test_validate_renders_table(mock_mcp, mock_config_cls):
    """bm schema validate renders a Rich table with results."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(cli_app, ["schema", "validate", "person"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "Schema Validation" in result.output
    assert "people/alice" in result.output
    assert "people/bob" in result.output
    assert "1/2 valid" in result.output
    mock_mcp.assert_called_once()
    assert mock_mcp.call_args.kwargs["output_format"] == "json"


@patch("basic_memory.cli.commands.schema.ConfigManager")
@patch(
    "basic_memory.cli.commands.schema.mcp_schema_validate",
    new_callable=AsyncMock,
    return_value=VALIDATE_REPORT,
)
def test_validate_strict_exits_on_errors(mock_mcp, mock_config_cls):
    """bm schema validate --strict exits with code 1 when errors exist."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(cli_app, ["schema", "validate", "person", "--strict"])

    assert result.exit_code == 1


@patch("basic_memory.cli.commands.schema.ConfigManager")
@patch(
    "basic_memory.cli.commands.schema.mcp_schema_validate",
    new_callable=AsyncMock,
    return_value={"error": "No notes found of type 'person'"},
)
def test_validate_error_response(mock_mcp, mock_config_cls):
    """bm schema validate shows error message from MCP tool."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(cli_app, ["schema", "validate", "person"])

    assert result.exit_code == 0
    assert "No notes found" in result.output


@patch("basic_memory.cli.commands.schema.ConfigManager")
@patch(
    "basic_memory.cli.commands.schema.mcp_schema_validate",
    new_callable=AsyncMock,
    return_value=VALIDATE_REPORT,
)
def test_validate_identifier_heuristic(mock_mcp, mock_config_cls):
    """bm schema validate treats target with / as identifier."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(cli_app, ["schema", "validate", "people/alice.md"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp.call_args.kwargs["identifier"] == "people/alice.md"
    assert mock_mcp.call_args.kwargs["note_type"] is None


# --- infer ---


@patch("basic_memory.cli.commands.schema.ConfigManager")
@patch(
    "basic_memory.cli.commands.schema.mcp_schema_infer",
    new_callable=AsyncMock,
    return_value=INFER_REPORT,
)
def test_infer_renders_table(mock_mcp, mock_config_cls):
    """bm schema infer renders frequency table and suggested schema."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(cli_app, ["schema", "infer", "person"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "Field Frequencies" in result.output
    assert "name" in result.output
    assert "Suggested schema" in result.output
    mock_mcp.assert_called_once()
    assert mock_mcp.call_args.kwargs["output_format"] == "json"


@patch("basic_memory.cli.commands.schema.ConfigManager")
@patch(
    "basic_memory.cli.commands.schema.mcp_schema_infer",
    new_callable=AsyncMock,
    return_value=INFER_REPORT,
)
def test_infer_threshold_passthrough(mock_mcp, mock_config_cls):
    """bm schema infer passes --threshold through to MCP tool."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(cli_app, ["schema", "infer", "person", "--threshold", "0.5"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp.call_args.kwargs["threshold"] == 0.5


@patch("basic_memory.cli.commands.schema.ConfigManager")
@patch(
    "basic_memory.cli.commands.schema.mcp_schema_infer",
    new_callable=AsyncMock,
    return_value={"error": "No schema pattern found for 'person' (threshold: 25%)"},
)
def test_infer_error_response(mock_mcp, mock_config_cls):
    """bm schema infer shows error message from MCP tool."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(cli_app, ["schema", "infer", "person"])

    assert result.exit_code == 0
    assert "No schema pattern found" in result.output


@patch("basic_memory.cli.commands.schema.ConfigManager")
@patch(
    "basic_memory.cli.commands.schema.mcp_schema_infer",
    new_callable=AsyncMock,
    return_value={
        "note_type": "person",
        "notes_analyzed": 0,
        "field_frequencies": [],
        "suggested_schema": {},
        "suggested_required": [],
        "suggested_optional": [],
        "excluded": [],
    },
)
def test_infer_zero_notes(mock_mcp, mock_config_cls):
    """bm schema infer shows message when zero notes found."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(cli_app, ["schema", "infer", "person"])

    assert result.exit_code == 0
    assert "No notes found" in result.output


# --- diff ---


@patch("basic_memory.cli.commands.schema.ConfigManager")
@patch(
    "basic_memory.cli.commands.schema.mcp_schema_diff",
    new_callable=AsyncMock,
    return_value=DIFF_REPORT_WITH_DRIFT,
)
def test_diff_renders_drift(mock_mcp, mock_config_cls):
    """bm schema diff shows new/dropped fields and cardinality changes."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(cli_app, ["schema", "diff", "person"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "drift detected" in result.output
    assert "email" in result.output
    assert "phone" in result.output
    assert "role: single -> array" in result.output
    mock_mcp.assert_called_once()
    assert mock_mcp.call_args.kwargs["output_format"] == "json"


@patch("basic_memory.cli.commands.schema.ConfigManager")
@patch(
    "basic_memory.cli.commands.schema.mcp_schema_diff",
    new_callable=AsyncMock,
    return_value=DIFF_REPORT_NO_DRIFT,
)
def test_diff_no_drift(mock_mcp, mock_config_cls):
    """bm schema diff shows success message when no drift found."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(cli_app, ["schema", "diff", "person"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "No drift detected" in result.output


@patch("basic_memory.cli.commands.schema.ConfigManager")
@patch(
    "basic_memory.cli.commands.schema.mcp_schema_diff",
    new_callable=AsyncMock,
    return_value={"error": "No schema found for type 'person'"},
)
def test_diff_error_response(mock_mcp, mock_config_cls):
    """bm schema diff shows error message from MCP tool."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(cli_app, ["schema", "diff", "person"])

    assert result.exit_code == 0
    assert "No schema found" in result.output


# --- Routing flags ---


def test_schema_routing_both_flags_error():
    """Schema commands exit with error when both --local and --cloud are specified."""
    result = runner.invoke(
        cli_app,
        ["schema", "validate", "person", "--local", "--cloud"],
    )

    assert result.exit_code == 1
