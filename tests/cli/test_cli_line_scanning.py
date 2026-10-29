"""CLI scanning flags and rendered coordinates survive the MCP boundary."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from basic_memory.cli.main import app


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
            "basic_memory.cli.commands.posix.resolve_output_mode",
            return_value=mode.removeprefix("--"),
        ),
    ):
        result = CliRunner().invoke(
            app,
            ["grep", "retry", "-C", "1", "--max-matches", "1"] + ([] if mode == "rich" else [mode]),
        )
    assert result.exit_code == 0, result.output
    assert tool.call_args.kwargs["semantic"] is False
    assert tool.call_args.kwargs["context_lines"] == 1
    assert tool.call_args.kwargs["max_matches"] == 1
    if mode == "--json":
        assert json.loads(result.stdout) == payload
    else:
        assert "runbook.md-3-context" in result.stdout
        assert "runbook.md:4:retry [safe]" in result.stdout
        assert "line 20" in result.stdout
        assert "--page 2" in result.stdout
