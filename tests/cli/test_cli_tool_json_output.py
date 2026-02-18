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
    "content": "---\ntitle: Test Note\ntags:\n- test\n---\n# Test Note\n\nhello world",
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
    assert (
        data["content"] == "---\ntitle: Test Note\ntags:\n- test\n---\n# Test Note\n\nhello world"
    )
    assert data["frontmatter"] == {"title": "Test Note", "tags": ["test"]}
    assert data["file_path"] == "notes/Test Note.md"
    mock_read_json.assert_called_once()


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_read_note",
)
def test_read_note_text_output(mock_mcp_read, mock_config_cls):
    """read-note with default text format uses the MCP tool path."""
    mock_config_cls.return_value = _mock_config_manager()

    mock_mcp_read.fn = AsyncMock(
        return_value="---\ntitle: Test Note\n---\n# Test Note\n\nhello world"
    )

    result = runner.invoke(
        cli_app,
        ["tool", "read-note", "test-note"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "---" in result.output
    mock_mcp_read.fn.assert_called_once()


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch("basic_memory.cli.commands.tool.mcp_read_note")
def test_read_note_workspace_passthrough(mock_mcp_read, mock_config_cls):
    """read-note --workspace passes workspace through to the MCP tool call."""
    mock_config_cls.return_value = _mock_config_manager()
    mock_mcp_read.fn = AsyncMock(return_value="# Test Note")

    result = runner.invoke(
        cli_app,
        ["tool", "read-note", "test-note", "--workspace", "tenant-123"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    mock_mcp_read.fn.assert_called_once()
    assert mock_mcp_read.fn.call_args.kwargs["workspace"] == "tenant-123"


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool._read_note_json",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT,
)
def test_read_note_json_strip_frontmatter(mock_read_json, mock_config_cls):
    """read-note --format json --strip-frontmatter strips content but keeps frontmatter object."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "read-note", "test-note", "--format", "json", "--strip-frontmatter"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["title"] == "Test Note"
    assert data["permalink"] == "notes/test-note"
    assert data["content"] == "# Test Note\n\nhello world"
    assert data["frontmatter"] == {"title": "Test Note", "tags": ["test"]}
    assert data["file_path"] == "notes/Test Note.md"
    mock_read_json.assert_called_once()


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_read_note",
)
def test_read_note_text_strip_frontmatter(mock_mcp_read, mock_config_cls):
    """read-note --strip-frontmatter strips opening frontmatter in text mode."""
    mock_config_cls.return_value = _mock_config_manager()

    mock_mcp_read.fn = AsyncMock(
        return_value="---\ntitle: Test Note\n---\n# Test Note\n\nhello world"
    )

    result = runner.invoke(
        cli_app,
        ["tool", "read-note", "test-note", "--strip-frontmatter"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "---" not in result.output
    assert "# Test Note" in result.output
    mock_mcp_read.fn.assert_called_once()


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool.mcp_read_note",
)
def test_read_note_text_strip_frontmatter_no_frontmatter(mock_mcp_read, mock_config_cls):
    """read-note --strip-frontmatter keeps notes unchanged when no frontmatter exists."""
    mock_config_cls.return_value = _mock_config_manager()

    mock_mcp_read.fn = AsyncMock(return_value="# Test Note\n\nhello world")

    result = runner.invoke(
        cli_app,
        ["tool", "read-note", "test-note", "--strip-frontmatter"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.output.strip() == "# Test Note\n\nhello world"
    mock_mcp_read.fn.assert_called_once()


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool._read_note_json",
    new_callable=AsyncMock,
    return_value={
        "title": "Test Note",
        "permalink": "notes/test-note",
        "content": "---\ntitle: [bad yaml\n# Test Note\n\nhello world",
        "file_path": "notes/Test Note.md",
    },
)
def test_read_note_json_malformed_frontmatter_kept(mock_read_json, mock_config_cls):
    """Malformed opening frontmatter should remain unchanged with frontmatter set to null."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "read-note", "test-note", "--format", "json", "--strip-frontmatter"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["content"] == "---\ntitle: [bad yaml\n# Test Note\n\nhello world"
    assert data["frontmatter"] is None
    mock_read_json.assert_called_once()


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool._read_note_json",
    new_callable=AsyncMock,
    return_value={
        "title": "No Frontmatter Note",
        "permalink": "notes/no-frontmatter-note",
        "content": "# No Frontmatter Note\n\nhello world",
        "file_path": "notes/No Frontmatter Note.md",
    },
)
def test_read_note_json_strip_frontmatter_no_frontmatter(mock_read_json, mock_config_cls):
    """JSON strip mode should keep content unchanged when no frontmatter exists."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "read-note", "test-note", "--format", "json", "--strip-frontmatter"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["content"] == "# No Frontmatter Note\n\nhello world"
    assert data["frontmatter"] is None
    mock_read_json.assert_called_once()


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


# --- read-note title fallback ---


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch(
    "basic_memory.cli.commands.tool._read_note_json",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT,
)
def test_read_note_json_with_plain_title(mock_read_json, mock_config_cls):
    """read-note --format json works with plain titles (not just permalinks)."""
    mock_config_cls.return_value = _mock_config_manager()

    result = runner.invoke(
        cli_app,
        ["tool", "read-note", "My Note Title", "--format", "json"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert data["title"] == "Test Note"
    # Verify the identifier was passed through
    call_args = mock_read_json.call_args
    assert call_args[0][0] == "My Note Title" or call_args[1].get("identifier") == "My Note Title"


# --- recent-activity pagination ---


@patch(
    "basic_memory.cli.commands.tool._recent_activity_json",
    new_callable=AsyncMock,
    return_value=RECENT_ACTIVITY_RESULT,
)
def test_recent_activity_json_pagination(mock_recent_json):
    """recent-activity --format json passes --page and --page-size to helper."""
    result = runner.invoke(
        cli_app,
        ["tool", "recent-activity", "--format", "json", "--page", "2", "--page-size", "10"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert isinstance(data, list)
    # Verify pagination params were passed through
    mock_recent_json.assert_called_once()
    call_kwargs = mock_recent_json.call_args.kwargs
    assert call_kwargs["page"] == 2
    assert call_kwargs["page_size"] == 10


# --- build-context --format json ---


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch("basic_memory.cli.commands.tool.mcp_build_context")
def test_build_context_format_json(mock_build_ctx, mock_config_cls):
    """build-context --format json outputs valid JSON."""
    mock_config_cls.return_value = _mock_config_manager()

    # build_context now returns a slimmed dict directly
    mock_build_ctx.fn = AsyncMock(
        return_value={
            "results": [],
            "metadata": {"uri": "test/topic", "depth": 1},
            "page": 1,
            "page_size": 10,
        }
    )

    result = runner.invoke(
        cli_app,
        ["tool", "build-context", "memory://test/topic", "--format", "json"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert "results" in data
    mock_build_ctx.fn.assert_called_once()


@patch("basic_memory.cli.commands.tool.ConfigManager")
@patch("basic_memory.cli.commands.tool.mcp_build_context")
def test_build_context_default_format_is_json(mock_build_ctx, mock_config_cls):
    """build-context defaults to JSON output (backward compatible)."""
    mock_config_cls.return_value = _mock_config_manager()

    # build_context now returns a slimmed dict directly
    mock_build_ctx.fn = AsyncMock(
        return_value={
            "results": [],
            "metadata": {"uri": "test/topic", "depth": 1},
            "page": 1,
            "page_size": 10,
        }
    )

    result = runner.invoke(
        cli_app,
        ["tool", "build-context", "memory://test/topic"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    data = json.loads(result.output)
    assert isinstance(data, dict)


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
