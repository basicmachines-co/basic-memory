"""Bug hunt regression test (#4): `bm tool search-notes` --category filter.

The MCP search_notes tool exposes a `categories` parameter for exact-match
observation-category filtering. The CLI wrapper had no equivalent flag. This
integration test asserts the CLI now exposes `--category` and that it filters
observation results to the requested category exactly.
"""

import json

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


def _write_note(title: str, folder: str, content: str) -> dict:
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
    return json.loads(result.stdout)


def test_search_notes_exposes_category_filter(app, app_config, test_project, config_manager):
    """CLI search-notes should expose --category like the MCP `categories` param."""
    _write_note(
        "Category Filter Note",
        "parity-category",
        "# Category Filter Note\n\n"
        "## Observations\n"
        "- [requirement] system must authenticate users CATTOKEN\n"
        "- [decision] use OAuth for auth CATTOKEN\n",
    )

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "search-notes",
            "CATTOKEN",
            "--project",
            test_project.name,
            "--entity-type",
            "observation",
            "--category",
            "requirement",
        ],
    )

    assert result.exit_code == 0, (
        "`--category` filter is not supported by the CLI search-notes command "
        "even though the MCP search_notes tool documents a `categories` param. "
        f"exit_code={result.exit_code} output={result.output}"
    )

    payload = json.loads(result.stdout)
    categories = {r.get("category") for r in payload.get("results", []) if r.get("category")}
    assert categories == {"requirement"}, (
        "--category requirement should return only requirement observations, "
        f"got categories={categories}"
    )
