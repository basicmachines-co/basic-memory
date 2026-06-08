"""Bug hunt regression test (#3): `bm tool recent-activity` page_size default.

The MCP recent_activity tool defaults page_size=10; the CLI wrapper used to
default to 50. Because page_size becomes the SQL LIMIT for the query, identical
default invocations returned a different number of rows from CLI vs MCP. This
integration test proves the CLI default now matches the MCP default of 10.
"""

import json

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()

MCP_DEFAULT_PAGE_SIZE = 10


def _write_note(title: str, folder: str, content: str) -> None:
    result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            title,
            "--folder",
            folder,
            "--content",
            content,
        ],
    )
    assert result.exit_code == 0, result.output


def test_recent_activity_default_page_size_matches_mcp(
    app, app_config, test_project, config_manager, monkeypatch
):
    """CLI recent-activity default page_size must match the MCP tool default (10)."""
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", test_project.name)

    for i in range(15):
        _write_note(
            f"Parity Note {i:02d}",
            "parity-recent",
            f"# Parity Note {i:02d}\n\nUnique body token PARITY{i:02d}.",
        )

    mcp_default_result = runner.invoke(
        cli_app,
        [
            "tool",
            "recent-activity",
            "--project",
            test_project.name,
            "--page-size",
            str(MCP_DEFAULT_PAGE_SIZE),
        ],
    )
    assert mcp_default_result.exit_code == 0, mcp_default_result.output
    mcp_default_rows = json.loads(mcp_default_result.stdout)
    assert len(mcp_default_rows) == MCP_DEFAULT_PAGE_SIZE

    cli_default_result = runner.invoke(
        cli_app,
        ["tool", "recent-activity", "--project", test_project.name],
    )
    assert cli_default_result.exit_code == 0, cli_default_result.output
    cli_default_rows = json.loads(cli_default_result.stdout)

    assert len(cli_default_rows) == MCP_DEFAULT_PAGE_SIZE, (
        f"CLI recent-activity default returned {len(cli_default_rows)} rows but "
        f"the MCP tool default (page_size={MCP_DEFAULT_PAGE_SIZE}) returns "
        f"{len(mcp_default_rows)}; the CLI and MCP default page_size must match."
    )
