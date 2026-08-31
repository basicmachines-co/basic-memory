"""Tests for `bm man apropos` and the manual-project fallback on `bm man <topic>` (#1404).

The bundled fast path stays DB-free; only a bundled miss reaches the MCP man
tool, and apropos always searches through it. Both are mocked here (the same
direct-tool-function pattern as the posix verb tests), so these tests stay
hermetic on machines with or without a "manual" project configured.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.exceptions import ToolError
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()

# man's query mode returns the search-notes response shape.
MAN_SEARCH_RESULT = {
    "total": 1,
    "total_is_exact": True,
    "current_page": 1,
    "page_size": 10,
    "has_more": False,
    "results": [
        {
            "type": "entity",
            "title": "cloud-sync(7)",
            "permalink": "man7/cloud-sync",
            "file_path": "man7/cloud-sync.md",
            "score": 0.9,
            "matched_chunk": "Conflict resolution during bisync",
            "content": None,
        }
    ],
}

MAN_SEARCH_EMPTY = {
    "total": 0,
    "total_is_exact": True,
    "current_page": 1,
    "page_size": 10,
    "has_more": False,
    "results": [],
}

MANUAL_NOTE_BODY = "# cloud-sync(7)\n\nHow bisync resolves conflicts."


def _tty_invoke(args, **kwargs):
    kwargs.setdefault("env", {"COLUMNS": "240"})
    with patch("basic_memory.cli.commands.tool._use_rich", return_value=True):
        return runner.invoke(cli_app, args, **kwargs)


def _flattened(output: str) -> str:
    return " ".join(output.split())


# ---------------------------------------------------------------------------
# apropos
# ---------------------------------------------------------------------------


@patch("basic_memory.mcp.tools.man", new_callable=AsyncMock, return_value=MAN_SEARCH_RESULT)
def test_apropos_non_tty_outputs_search_payload_json(mock_man):
    result = runner.invoke(cli_app, ["man", "apropos", "conflict resolution"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == MAN_SEARCH_RESULT
    mock_man.assert_called_once()
    assert mock_man.call_args.kwargs["query"] == "conflict resolution"
    assert mock_man.call_args.kwargs["project"] is None


@patch("basic_memory.mcp.tools.man", new_callable=AsyncMock, return_value=MAN_SEARCH_RESULT)
def test_apropos_rich_renders_search_table(mock_man):
    result = _tty_invoke(["man", "apropos", "conflict"])

    assert result.exit_code == 0, result.output
    with pytest.raises((json.JSONDecodeError, ValueError)):
        json.loads(result.output)
    assert "cloud-sync(7)" in result.output
    assert "man7/cloud-sync" in _flattened(result.output)


@patch("basic_memory.mcp.tools.man", new_callable=AsyncMock, return_value=MAN_SEARCH_RESULT)
def test_apropos_plain_output(mock_man):
    result = _tty_invoke(["man", "apropos", "conflict", "--plain"])

    assert result.exit_code == 0, result.output
    assert "1. cloud-sync(7)" in result.output
    assert "─" not in result.output
    assert "│" not in result.output


@patch("basic_memory.mcp.tools.man", new_callable=AsyncMock, return_value=MAN_SEARCH_EMPTY)
def test_apropos_empty_results_is_a_successful_search(mock_man):
    result = _tty_invoke(["man", "apropos", "nothing here"])

    assert result.exit_code == 0, result.output
    assert "No results found" in result.output


@patch("basic_memory.mcp.tools.man", new_callable=AsyncMock, return_value=MANUAL_NOTE_BODY)
def test_apropos_string_result_falls_back_to_json(mock_man):
    """A string payload has no table shape to render, so it prints as JSON."""
    result = _tty_invoke(["man", "apropos", "conflict"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == MANUAL_NOTE_BODY


@patch("basic_memory.mcp.tools.man", new_callable=AsyncMock, return_value=MAN_SEARCH_RESULT)
def test_apropos_project_passthrough(mock_man):
    result = runner.invoke(cli_app, ["man", "apropos", "conflict", "--project", "docs"])

    assert result.exit_code == 0, result.output
    assert mock_man.call_args.kwargs["project"] == "docs"


@patch("basic_memory.mcp.tools.man", new_callable=AsyncMock, return_value=MAN_SEARCH_RESULT)
def test_apropos_json_and_plain_together_errors(mock_man):
    result = _tty_invoke(["man", "apropos", "conflict", "--json", "--plain"])

    assert result.exit_code == 1
    assert "mutually exclusive" in result.output
    mock_man.assert_not_called()


@patch("basic_memory.mcp.tools.man", new_callable=AsyncMock, return_value=MAN_SEARCH_RESULT)
def test_apropos_local_and_cloud_together_errors(mock_man):
    result = runner.invoke(cli_app, ["man", "apropos", "conflict", "--local", "--cloud"])

    assert result.exit_code == 1
    assert "Cannot specify both --local and --cloud" in result.output
    mock_man.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(ToolError("manual search failed"), id="tool-error"),
        pytest.param(ValueError("Project not found: manual"), id="no-manual-project"),
        pytest.param(
            RuntimeError(
                "Cloud routing requested but no credentials found. "
                "Run 'bm cloud api-key save <key>' or 'bm cloud login' first."
            ),
            id="no-creds",
        ),
    ],
)
def test_apropos_tool_failures_name_the_manual_project(error):
    """Every unreachable-manual flavor leads with the man context, not just the raw error.

    On a fresh local install the raw routing error demands cloud credentials
    the user never asked for; the hint says what apropos actually searches and
    where the bundled pages live. The underlying text still prints underneath,
    because for a genuinely cloud-configured install its setup hint is the fix.
    """
    with patch("basic_memory.mcp.tools.man", new_callable=AsyncMock, side_effect=error):
        result = runner.invoke(cli_app, ["man", "apropos", "conflict"])

    assert result.exit_code == 1
    flat = _flattened(result.output)
    assert "apropos searches the 'manual' project" in flat
    assert "bm man list" in flat
    assert "Error:" in result.output
    assert str(error) in result.output


def test_apropos_failure_hint_names_the_overridden_project():
    """A --project override failure points at that project, not the default manual."""
    error = ValueError("Project not found: docs")
    with patch("basic_memory.mcp.tools.man", new_callable=AsyncMock, side_effect=error):
        result = runner.invoke(cli_app, ["man", "apropos", "conflict", "--project", "docs"])

    assert result.exit_code == 1
    assert "apropos searches the 'docs' project" in _flattened(result.output)


# ---------------------------------------------------------------------------
# show: bundled fast path vs manual-project fallback
# ---------------------------------------------------------------------------


@patch("basic_memory.mcp.tools.man", new_callable=AsyncMock, return_value=MANUAL_NOTE_BODY)
def test_show_bundled_miss_falls_back_to_manual_note(mock_man):
    """A topic that is no bundled page reads as a note from the manual project."""
    result = runner.invoke(cli_app, ["man", "show", "cloud-sync"])

    assert result.exit_code == 0, result.output
    assert result.stdout == MANUAL_NOTE_BODY + "\n"
    mock_man.assert_called_once()
    assert mock_man.call_args.kwargs["page"] == "cloud-sync"
    assert mock_man.call_args.kwargs["project"] is None


@patch("basic_memory.mcp.tools.man", new_callable=AsyncMock, return_value=MANUAL_NOTE_BODY)
def test_topic_dispatch_reaches_fallback_for_unparseable_references(mock_man):
    """`bm man docs/guide` is no page reference, but may still name a manual note."""
    result = runner.invoke(cli_app, ["man", "docs/guide"])

    assert result.exit_code == 0, result.output
    assert result.stdout == MANUAL_NOTE_BODY + "\n"
    assert mock_man.call_args.kwargs["page"] == "docs/guide"


@patch("basic_memory.mcp.tools.man", new_callable=AsyncMock, return_value=MANUAL_NOTE_BODY)
def test_show_bundled_hit_never_calls_the_tool(mock_man):
    """Bundled pages resolve without the MCP stack: a broken DB can't block docs."""
    result = runner.invoke(cli_app, ["man", "search-notes"])

    assert result.exit_code == 0, result.output
    assert result.output.startswith("# search-notes(3)\n")
    mock_man.assert_not_called()


@patch("basic_memory.mcp.tools.man", new_callable=AsyncMock, return_value=MANUAL_NOTE_BODY)
def test_show_bundled_verb_page_is_local(mock_man):
    """The new section-1 verb pages are bundled: `bm man cat` stays local too."""
    result = runner.invoke(cli_app, ["man", "cat"])

    assert result.exit_code == 0, result.output
    assert result.output.startswith("# cat(1)\n")
    mock_man.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(ToolError("No manual entry for missing-topic"), id="resolve-miss"),
        pytest.param(ValueError("Project not found: manual"), id="no-manual-project"),
        pytest.param(RuntimeError("Cloud project 'manual': no credentials found"), id="no-creds"),
    ],
)
def test_show_fallback_failures_degrade_to_the_miss_hint(error):
    """Every unreachable-manual flavor prints the man(1)-style miss, not a traceback."""
    with patch("basic_memory.mcp.tools.man", new_callable=AsyncMock, side_effect=error):
        result = runner.invoke(cli_app, ["man", "missing-topic"])

    assert result.exit_code == 1
    flat = _flattened(result.output)
    assert "No manual entry for missing-topic" in flat
    assert "bm man list" in flat


def test_show_local_and_cloud_together_errors():
    result = runner.invoke(cli_app, ["man", "show", "search-notes", "--local", "--cloud"])

    assert result.exit_code == 1
    assert "Cannot specify both --local and --cloud" in result.output
