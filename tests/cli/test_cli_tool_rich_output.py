"""Tests for Rich (human-readable) output mode for bm tool commands.

Commands default to Rich output when stdout is a TTY and fall back to raw JSON
when stdout is piped or --json is supplied.  These tests verify both modes.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Shared mock payloads (mirrors test_cli_tool_json_output.py for symmetry)
# ---------------------------------------------------------------------------

READ_NOTE_RESULT = {
    "title": "Test Note",
    "permalink": "notes/test-note",
    "file_path": "notes/Test Note.md",
    "content": "# Test Note\n\nhello world",
    "frontmatter": {"title": "Test Note", "tags": ["test"]},
}

SEARCH_RESULT = {
    "query": "test",
    "total": 2,
    "page": 1,
    "page_size": 10,
    "results": [
        {
            "type": "entity",
            "title": "Test Note",
            "permalink": "notes/test-note",
            "file_path": "notes/Test Note.md",
        },
        {
            "type": "observation",
            "title": "Another Note",
            "permalink": "notes/another-note",
            "file_path": "notes/Another Note.md",
        },
    ],
}

SEARCH_RESULT_EMPTY = {
    "query": "nothing",
    "total": 0,
    "page": 1,
    "page_size": 10,
    "results": [],
}

BUILD_CONTEXT_RESULT = {
    "results": [
        {
            "type": "entity",
            "title": "Related Note",
            "permalink": "notes/related",
            "relation_type": "references",
        }
    ],
    "metadata": {"uri": "notes/test-note", "depth": 1},
    "page": 1,
    "page_size": 10,
}

BUILD_CONTEXT_EMPTY = {
    "results": [],
    "metadata": {"uri": "notes/test-note", "depth": 1},
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
        "updated_at": "2025-01-01 12:00:00",
    },
    {
        "type": "entity",
        "title": "Note B",
        "permalink": "notes/note-b",
        "file_path": "notes/Note B.md",
        "created_at": "2025-01-02 00:00:00",
        "updated_at": None,
    },
]


# ---------------------------------------------------------------------------
# Helper: simulate a TTY by patching _use_rich to return True
# ---------------------------------------------------------------------------


def _tty_runner(args, **kwargs):
    """Invoke CLI as if stdout is a TTY (Rich output enabled)."""
    with patch("basic_memory.cli.commands.tool._use_rich", return_value=True):
        return runner.invoke(cli_app, args, **kwargs)


# ---------------------------------------------------------------------------
# search-notes – Rich output
# ---------------------------------------------------------------------------


@patch(
    "basic_memory.cli.commands.tool.mcp_search",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT,
)
def test_search_notes_rich_output_default(mock_mcp):
    """search-notes produces Rich table output when stdout is a TTY."""
    result = _tty_runner(["tool", "search-notes", "test"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    # Rich output should NOT be valid JSON
    with pytest.raises((json.JSONDecodeError, ValueError)):
        json.loads(result.output)
    # But it should contain the result titles
    assert "Test Note" in result.output
    assert "Another Note" in result.output
    assert "notes/test-note" in result.output


@patch(
    "basic_memory.cli.commands.tool.mcp_search",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT_EMPTY,
)
def test_search_notes_rich_empty(mock_mcp):
    """search-notes Rich output handles empty results gracefully."""
    result = _tty_runner(["tool", "search-notes", "nothing"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "No results found" in result.output


@patch(
    "basic_memory.cli.commands.tool.mcp_search",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT,
)
def test_search_notes_json_flag_overrides_tty(mock_mcp):
    """search-notes --json outputs raw JSON even when stdout is a TTY."""
    result = _tty_runner(["tool", "search-notes", "test", "--json"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["total"] == 2
    assert data["results"][0]["title"] == "Test Note"


@patch(
    "basic_memory.cli.commands.tool.mcp_search",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT,
)
def test_search_notes_non_tty_gives_json(mock_mcp):
    """search-notes outputs JSON when stdout is not a TTY (default runner behaviour)."""
    # CliRunner does not set isatty(); _use_rich() returns False → JSON path.
    result = runner.invoke(cli_app, ["tool", "search-notes", "test"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["total"] == 2


# ---------------------------------------------------------------------------
# read-note – Rich output
# ---------------------------------------------------------------------------


@patch(
    "basic_memory.cli.commands.tool.mcp_read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT,
)
def test_read_note_rich_output_default(mock_mcp):
    """read-note produces Rich formatted output when stdout is a TTY."""
    result = _tty_runner(["tool", "read-note", "test-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    # Rich output contains the note title
    assert "Test Note" in result.output
    # And the rendered markdown content
    assert "hello world" in result.output
    # Not raw JSON
    with pytest.raises((json.JSONDecodeError, ValueError)):
        json.loads(result.output)


@patch(
    "basic_memory.cli.commands.tool.mcp_read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT,
)
def test_read_note_json_flag_overrides_tty(mock_mcp):
    """read-note --json outputs raw JSON even when stdout is a TTY."""
    result = _tty_runner(["tool", "read-note", "test-note", "--json"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["title"] == "Test Note"
    assert data["content"] == "# Test Note\n\nhello world"


@patch(
    "basic_memory.cli.commands.tool.mcp_read_note",
    new_callable=AsyncMock,
    return_value={"title": "", "permalink": "", "content": "", "frontmatter": {}},
)
def test_read_note_rich_empty_content(mock_mcp):
    """read-note Rich output handles empty content without crashing."""
    result = _tty_runner(["tool", "read-note", "empty-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "no content" in result.output.lower()


@patch(
    "basic_memory.cli.commands.tool.mcp_read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT,
)
def test_read_note_non_tty_gives_json(mock_mcp):
    """read-note outputs JSON when stdout is not a TTY."""
    result = runner.invoke(cli_app, ["tool", "read-note", "test-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["title"] == "Test Note"


# ---------------------------------------------------------------------------
# build-context – Rich output
# ---------------------------------------------------------------------------


@patch(
    "basic_memory.cli.commands.tool.mcp_build_context",
    new_callable=AsyncMock,
    return_value=BUILD_CONTEXT_RESULT,
)
def test_build_context_rich_output_default(mock_mcp):
    """build-context produces Rich tree output when stdout is a TTY."""
    result = _tty_runner(["tool", "build-context", "memory://notes/test-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "notes/test-note" in result.output
    assert "Related Note" in result.output
    # Not raw JSON
    with pytest.raises((json.JSONDecodeError, ValueError)):
        json.loads(result.output)


@patch(
    "basic_memory.cli.commands.tool.mcp_build_context",
    new_callable=AsyncMock,
    return_value=BUILD_CONTEXT_EMPTY,
)
def test_build_context_rich_empty(mock_mcp):
    """build-context Rich output handles empty results gracefully."""
    result = _tty_runner(["tool", "build-context", "memory://notes/test-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "No related content found" in result.output


@patch(
    "basic_memory.cli.commands.tool.mcp_build_context",
    new_callable=AsyncMock,
    return_value=BUILD_CONTEXT_RESULT,
)
def test_build_context_json_flag_overrides_tty(mock_mcp):
    """build-context --json outputs raw JSON even when stdout is a TTY."""
    result = _tty_runner(["tool", "build-context", "memory://notes/test-note", "--json"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert "results" in data
    assert data["results"][0]["title"] == "Related Note"


@patch(
    "basic_memory.cli.commands.tool.mcp_build_context",
    new_callable=AsyncMock,
    return_value=BUILD_CONTEXT_RESULT,
)
def test_build_context_non_tty_gives_json(mock_mcp):
    """build-context outputs JSON when stdout is not a TTY."""
    result = runner.invoke(cli_app, ["tool", "build-context", "memory://notes/test-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert "results" in data


# ---------------------------------------------------------------------------
# recent-activity – Rich output
# ---------------------------------------------------------------------------


@patch(
    "basic_memory.cli.commands.tool.mcp_recent_activity",
    new_callable=AsyncMock,
    return_value=RECENT_ACTIVITY_RESULT,
)
def test_recent_activity_rich_output_default(mock_mcp):
    """recent-activity produces Rich table output when stdout is a TTY."""
    result = _tty_runner(["tool", "recent-activity"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "Note A" in result.output
    assert "Note B" in result.output
    assert "notes/note-a" in result.output
    # Not raw JSON
    with pytest.raises((json.JSONDecodeError, ValueError)):
        json.loads(result.output)


@patch(
    "basic_memory.cli.commands.tool.mcp_recent_activity",
    new_callable=AsyncMock,
    return_value=[],
)
def test_recent_activity_rich_empty(mock_mcp):
    """recent-activity Rich output handles empty results gracefully."""
    result = _tty_runner(["tool", "recent-activity"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "No recent activity" in result.output


@patch(
    "basic_memory.cli.commands.tool.mcp_recent_activity",
    new_callable=AsyncMock,
    return_value=RECENT_ACTIVITY_RESULT,
)
def test_recent_activity_json_flag_overrides_tty(mock_mcp):
    """recent-activity --json outputs raw JSON even when stdout is a TTY."""
    result = _tty_runner(["tool", "recent-activity", "--json"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["title"] == "Note A"


@patch(
    "basic_memory.cli.commands.tool.mcp_recent_activity",
    new_callable=AsyncMock,
    return_value=RECENT_ACTIVITY_RESULT,
)
def test_recent_activity_non_tty_gives_json(mock_mcp):
    """recent-activity outputs JSON when stdout is not a TTY."""
    result = runner.invoke(cli_app, ["tool", "recent-activity"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 2
