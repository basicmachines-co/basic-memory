"""Tests for --format json output in CLI tool commands.

Verifies that write-note, read-note, and recent-activity commands
produce valid JSON output when invoked with --format json, and that
the default text format still works via the MCP tool path.
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
    "content": "hello world",
    "file_path": "notes/Test Note.md",
}

READ_NOTE_RESULT = {
    "title": "Test Note",
    "permalink": "notes/test-note",
    "content": "# Test Note\n\nhello world",
    "file_path": "notes/Test Note.md",
}

RECENT_ACTIVITY_RESULT = [
    {
        "title": "Note A",
        "permalink": "notes/note-a",
        "file_path": "notes/Note A.md",
        "created_at": "2025-01-01 00:00:00",
    },
    {
        "title": "Note B",
        "permalink": "notes/note-b",
        "file_path": "notes/Note B.md",
        "created_at": "2025-01-02 00:00:00",
    },
]


def _mock_config_manager():
    """Create a mock ConfigManager that avoids reading real config."""
    mock_cm = MagicMock()
    mock_cm.config = MagicMock()
    mock_cm.default_project = "test-project"
    mock_cm.get_project.return_value = ("test-project", "/tmp/test")
    return mock_cm


# --- write-note --format json ---


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool._write_note_json",
    new_callable=AsyncMock,
    return_value=WRITE_NOTE_RESULT,
)
def test_write_note_json_output(mock_write_json, mock_config_cls):
    """write-note --format json outputs valid JSON with expected keys."""
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
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["title"] == "Test Note"
    assert data["permalink"] == "notes/test-note"
    assert data["content"] == "hello world"
    assert data["file_path"] == "notes/Test Note.md"
    mock_write_json.assert_called_once()


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_write_note",
)
def test_write_note_text_output(mock_mcp_write, mock_config_cls):
    """write-note with default text format uses the MCP tool path."""
    mock_config_cls.return_value = _mock_config_manager()

    # MCP tool .fn returns a formatted string
    mock_mcp_write.fn = AsyncMock(return_value="Created note: Test Note")

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
    assert "Created note: Test Note" in result.output
    mock_mcp_write.fn.assert_called_once()


# --- read-note --format json ---


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool._read_note_json",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT,
)
def test_read_note_json_output(mock_read_json, mock_config_cls):
    """read-note --format json outputs valid JSON with expected keys."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "read-note", "test-note", "--format", "json"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["title"] == "Test Note"
    assert data["permalink"] == "notes/test-note"
    assert data["content"] == "# Test Note\n\nhello world"
    assert data["file_path"] == "notes/Test Note.md"
    mock_read_json.assert_called_once()


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_read_note",
)
def test_read_note_text_output(mock_mcp_read, mock_config_cls):
    """read-note with default text format uses the MCP tool path."""
    mock_config_cls.return_value = _mock_config_manager()

    mock_mcp_read.fn = AsyncMock(return_value="# Test Note\n\nhello world")

    result = runner.invoke(
        cli_app,
        ["tool", "read-note", "test-note"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "Test Note" in result.output
    mock_mcp_read.fn.assert_called_once()


# --- recent-activity --format json ---


@patch(
    "basic_memory.cli.commands.tool._recent_activity_json",
    new_callable=AsyncMock,
    return_value=RECENT_ACTIVITY_RESULT,
)
def test_recent_activity_json_output(mock_recent_json):
    """recent-activity --format json outputs valid JSON list."""
    result = runner.invoke(
        cli_app,
        ["tool", "recent-activity", "--format", "json"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["title"] == "Note A"
    assert data[0]["permalink"] == "notes/note-a"
    assert data[0]["file_path"] == "notes/Note A.md"
    assert data[0]["created_at"] == "2025-01-01 00:00:00"
    assert data[1]["title"] == "Note B"
    mock_recent_json.assert_called_once()


@patch(
    "basic_memory.cli.commands.tool.mcp_recent_activity",
)
def test_recent_activity_text_output(mock_mcp_recent):
    """recent-activity with default text format uses the MCP tool path."""
    mock_mcp_recent.fn = AsyncMock(return_value="Recent activity:\n- Note A\n- Note B")

    result = runner.invoke(
        cli_app,
        ["tool", "recent-activity"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "Recent activity:" in result.output
    mock_mcp_recent.fn.assert_called_once()


# --- Edge cases ---


@patch(
    "basic_memory.cli.commands.tool._recent_activity_json",
    new_callable=AsyncMock,
    return_value=[],
)
def test_recent_activity_json_empty(mock_recent_json):
    """recent-activity --format json handles empty results."""
    result = runner.invoke(
        cli_app,
        ["tool", "recent-activity", "--format", "json"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data == []
