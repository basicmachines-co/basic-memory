"""The SessionStart brief's searches against a real project (#2101).

The brief searches through the project API's typed client instead of the bm_search
tool, so a SessionStart does not import the MCP tool stack. These tests hold the two
to the same answer and keep the import boundary in place.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from basic_memory.cli.commands import hook as hook_module
from basic_memory.mcp.tools import bm_search, bm_write


async def _write_note(project: str, title: str, note_type: str, status: str) -> None:
    await bm_write(
        project=project,
        title=title,
        directory="brief",
        note_type=note_type,
        metadata={"status": status},
        content=f"# {title}\n\nBody of {title}.",
    )


@pytest.mark.asyncio
async def test_brief_search_matches_bm_search_for_the_same_filters(client, test_project):
    await _write_note(test_project.name, "Use SQLite", "decision", "open")
    await _write_note(test_project.name, "Use Redis", "decision", "superseded")
    await _write_note(test_project.name, "Ship the hook", "task", "open")

    brief = await hook_module._query(
        test_project.name, note_types=["decision"], filters={"status": "open"}, since="7d"
    )
    tool = await bm_search(
        project=test_project.name,
        note_types=["decision"],
        filters={"status": "open"},
        since="7d",
        page_size=5,
        output_format="json",
    )

    assert brief is not None and isinstance(tool, dict)
    assert [row["title"] for row in brief["results"]] == ["Use SQLite"]
    assert [row["permalink"] for row in brief["results"]] == [
        row["permalink"] for row in tool["results"]
    ]


@pytest.mark.asyncio
async def test_brief_search_reads_an_unknown_project_as_no_data(client, test_project):
    assert await hook_module._query("no-such-project", note_types=["decision"]) is None


def test_session_start_never_imports_the_mcp_tool_stack(tmp_path: Path) -> None:
    """Loading every tool and the MCP server cost a SessionStart its timeout (#2101)."""
    probe = (
        "import asyncio, sys\n"
        "from basic_memory.cli.commands import hook\n"
        "result = asyncio.run(hook._query(None, note_types=['decision']))\n"
        "assert result is not None, 'the search did not run'\n"
        "loaded = [m for m in ('basic_memory.mcp.tools', 'basic_memory.mcp.server',"
        " 'fastmcp.server') if m in sys.modules]\n"
        "assert not loaded, loaded\n"
    )
    # A fresh home keeps the probe off the developer's real config and projects.
    home = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env={**os.environ, **home},
    )
    assert result.returncode == 0, result.stderr
