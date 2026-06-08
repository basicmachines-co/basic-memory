"""Bughunt regression tests: pagination + project-name display in navigation tools.

These integration tests come from the integration-test bug hunt. They exercise the
real MCP server (FastMCP Client), real DB, and real ASGI routing — no mocks.

Covered bugs:
- #9  search_notes accepted non-positive page_size and returned a misleading
      has_more=true with zero rows (inconsistent with recent_activity validation).
- #10 build_context with page_size<=0 silently dropped the requested primary entity
      (primary_count=0 for a valid memory:// URL).
- #13 recent_activity text output printed the raw project UUID instead of the
      project name when routed via project_id.
"""

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


def _parse(result):
    return json.loads(result.content[0].text)


async def _seed_notes(client, project, n=15):
    for i in range(n):
        await client.call_tool(
            "write_note",
            {
                "project": project,
                "title": f"Pg Note {i + 1:02d}",
                "directory": "pg",
                "content": f"# Pg Note {i + 1:02d}\n\npagination probe content.",
                "tags": "pg,probe",
            },
        )


# --- Bug #9: search_notes pagination validation ---


@pytest.mark.asyncio
async def test_search_notes_rejects_nonpositive_page_size(mcp_server, app, test_project):
    """search_notes must reject non-positive page_size like recent_activity does,
    instead of returning a misleading has_more=true with zero rows."""
    async with Client(mcp_server) as client:
        await _seed_notes(client, test_project.name, 15)

        # Baseline: recent_activity correctly rejects page_size < 1.
        with pytest.raises(ToolError, match="page_size"):
            await client.call_tool(
                "recent_activity",
                {"project": test_project.name, "page_size": -3, "output_format": "json"},
            )

        # search_notes must now reject page_size=0 with the same guard, rather than
        # returning an empty payload that misleadingly claims more results exist.
        with pytest.raises(ToolError, match="page_size"):
            await client.call_tool(
                "search_notes",
                {
                    "project": test_project.name,
                    "query": "pagination",
                    "search_type": "text",
                    "page": 1,
                    "page_size": 0,
                    "output_format": "json",
                },
            )

        # Negative page_size must not return an arbitrary uncapped slice — also rejected.
        with pytest.raises(ToolError, match="page_size"):
            await client.call_tool(
                "search_notes",
                {
                    "project": test_project.name,
                    "query": "pagination",
                    "search_type": "text",
                    "page": 1,
                    "page_size": -3,
                    "output_format": "json",
                },
            )

        # page < 1 is rejected too.
        with pytest.raises(ToolError, match="page"):
            await client.call_tool(
                "search_notes",
                {
                    "project": test_project.name,
                    "query": "pagination",
                    "search_type": "text",
                    "page": 0,
                    "page_size": 5,
                    "output_format": "json",
                },
            )

        # A valid page_size still works and reports honest pagination.
        ok = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "pagination",
                "search_type": "text",
                "page": 1,
                "page_size": 5,
                "output_format": "json",
            },
        )
        d_ok = _parse(ok)
        assert len(d_ok["results"]) == 5
        assert d_ok["has_more"] is True


# --- Bug #9 (CLI surface): search-notes --page-size 0 ---


def _write_cli(title, folder="pgcli"):
    return runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            title,
            "--folder",
            folder,
            "--content",
            f"# {title}\n\nclipagination probe body.",
        ],
    )


def test_cli_search_notes_page_size_zero(app, app_config, test_project, config_manager):
    """`bm tool search-notes ... --page-size 0` must fail fast instead of returning a
    misleading has_more=true with zero rows."""
    for i in range(5):
        w = _write_cli(f"CliPg Note {i:02d}")
        assert w.exit_code == 0, w.output

    res = runner.invoke(
        cli_app,
        ["tool", "search-notes", "clipagination", "--local", "--page-size", "0"],
    )
    # The fix raises ValueError, which the CLI maps to a non-zero exit with a clear
    # message — the faithful "no misleading pagination signal" outcome at the CLI.
    assert res.exit_code != 0, res.output
    assert "page_size" in res.output


# --- Bug #10: build_context must always return the requested primary entity ---


@pytest.mark.asyncio
async def test_build_context_nonpositive_page_size_drops_primary(mcp_server, app, test_project):
    """build_context with a valid URL must return its primary entity (or raise) —
    a non-positive page_size must never silently drop it."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Primary Note",
                "directory": "ctx",
                "content": "# Primary Note\n\n## Relations\n- relates_to [[Other Note]]\n",
                "tags": "ctx",
            },
        )
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Other Note",
                "directory": "ctx",
                "content": "# Other Note\n\nrelated content.\n",
                "tags": "ctx",
            },
        )

        # Sanity: a normal page_size returns the primary note.
        r_ok = await client.call_tool(
            "build_context",
            {"project": test_project.name, "url": "ctx/primary-note", "output_format": "json"},
        )
        d_ok = _parse(r_ok)
        assert d_ok["metadata"]["primary_count"] == 1

        # page_size=0 must NOT silently drop the requested entity. The fix rejects it
        # with a clear error rather than returning primary_count=0.
        with pytest.raises(ToolError, match="page_size"):
            await client.call_tool(
                "build_context",
                {
                    "project": test_project.name,
                    "url": "ctx/primary-note",
                    "page_size": 0,
                    "output_format": "json",
                },
            )


# --- Bug #13: recent_activity header shows project name, not raw UUID ---


@pytest.mark.asyncio
async def test_recent_activity_project_id_header_shows_name_not_uuid(
    mcp_server, app, test_project, config_manager
):
    """When routed via project_id, the text header must name the project, not the UUID."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "RA Header Note",
                "directory": "ra",
                "content": "# RA Header Note\n\nToken",
                "output_format": "json",
            },
        )

        result = await client.call_tool(
            "recent_activity",
            {"project_id": test_project.external_id, "output_format": "text"},
        )
        text = result.content[0].text

        # The human-readable header should reference the project NAME, not the UUID.
        assert test_project.external_id not in text, (
            f"recent_activity header leaked the raw external_id UUID:\n{text[:300]}"
        )
        assert test_project.name in text, (
            f"recent_activity header should contain the project name:\n{text[:300]}"
        )
