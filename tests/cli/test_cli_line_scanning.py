"""CLI scanning flags and rendered coordinates survive the MCP boundary."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from basic_memory.cli.main import app


@pytest.mark.parametrize("mode", ["--plain", "--json", "rich"])
def test_cli_read_lines(mode: str) -> None:
    payload = {
        "title": "Runbook",
        "file_path": "runbook.md",
        "permalink": "runbook",
        "content": "retry [safe]\n",
        "frontmatter": None,
        "start_line": 4,
        "end_line": 5,
        "total_lines": 8,
        "has_more": True,
        "next_start_line": 6,
        "next_end_line": 7,
    }
    tool = AsyncMock(return_value=payload)
    with (
        patch("basic_memory.mcp.tools.read_note", tool),
        patch(
            "basic_memory.cli.commands.tool._resolve_output_mode",
            return_value=mode.removeprefix("--"),
        ),
    ):
        result = CliRunner().invoke(
            app,
            ["tool", "read-note", "runbook", "--start-line", "4", "--end-line", "5"]
            + ([] if mode == "rich" else [mode]),
        )
    assert result.exit_code == 0, result.output
    assert tool.call_args.kwargs["start_line"] == 4
    assert tool.call_args.kwargs["end_line"] == 5
    if mode == "--json":
        assert json.loads(result.stdout) == payload
    else:
        assert "4: retry [safe]" in result.stdout
        assert "start_line=6" in result.stdout


@pytest.mark.parametrize("mode", ["--plain", "--json", "rich"])
def test_cli_grep_context(mode: str) -> None:
    payload = {
        "current_page": 1,
        "page_size": 10,
        "total": 20,
        "has_more": True,
        "pagination_scope": "search_candidates",
        "results": [
            {
                "file_path": "runbook.md",
                "match_count": 2,
                "next_match_line": 20,
                "windows": [
                    {
                        "start_line": 3,
                        "end_line": 4,
                        "match_lines": [4],
                        "content": "context\nretry [safe]",
                    }
                ],
            }
        ],
    }
    tool = AsyncMock(return_value=payload)
    with (
        patch("basic_memory.mcp.tools.grep", tool),
        patch(
            "basic_memory.cli.commands.posix._resolve_output_mode",
            return_value=mode.removeprefix("--"),
        ),
    ):
        result = CliRunner().invoke(
            app,
            ["grep", "retry", "-F", "-C", "1", "--max-matches", "1"]
            + ([] if mode == "rich" else [mode]),
        )
    assert result.exit_code == 0, result.output
    assert tool.call_args.kwargs["literal"] is True
    assert tool.call_args.kwargs["context_lines"] == 1
    assert tool.call_args.kwargs["max_matches"] == 1
    if mode == "--json":
        assert json.loads(result.stdout) == payload
    else:
        assert "runbook.md-3-context" in result.stdout
        assert "runbook.md:4:retry [safe]" in result.stdout
        assert "line 20" in result.stdout
        assert "--page 2" in result.stdout
