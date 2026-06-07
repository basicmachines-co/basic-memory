"""Integration coverage for `bm tool write-note --type` (Issue #875)."""

import json

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


def test_write_note_type_flag_round_trip(app, app_config, test_project, config_manager):
    """`--type` sets the persisted note type and is searchable via `--type`."""
    write_result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "CLI Typed Note",
            "--folder",
            "typed",
            "--content",
            "# CLI Typed Note\n\nCliTypeToken body.",
            "--type",
            "guide",
        ],
    )
    assert write_result.exit_code == 0, write_result.output
    write_data = json.loads(write_result.stdout)
    permalink = write_data["permalink"]

    # Read back the frontmatter to confirm the persisted type.
    read_result = runner.invoke(
        cli_app,
        ["tool", "read-note", permalink, "--include-frontmatter"],
    )
    assert read_result.exit_code == 0, read_result.output
    read_data = json.loads(read_result.stdout)
    assert read_data["frontmatter"]["type"] == "guide"

    # The search note-type filter must return the typed note.
    search_result = runner.invoke(
        cli_app,
        [
            "tool",
            "search-notes",
            "CliTypeToken",
            "--type",
            "guide",
            "--local",
            "--page-size",
            "20",
        ],
    )
    assert search_result.exit_code == 0, search_result.output
    search_data = json.loads(search_result.stdout)
    permalinks = {item["permalink"] for item in search_data["results"]}
    assert permalink in permalinks


def test_write_note_content_frontmatter_type_wins_over_flag(
    app, app_config, test_project, config_manager
):
    """A `type:` in content frontmatter takes precedence over `--type` (documented behavior)."""
    content = "---\ntype: session\n---\n# Frontmatter Wins\n\nFrontmatterWinsToken body."

    write_result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Frontmatter Wins",
            "--folder",
            "typed",
            "--content",
            content,
            "--type",
            "guide",
        ],
    )
    assert write_result.exit_code == 0, write_result.output
    write_data = json.loads(write_result.stdout)
    permalink = write_data["permalink"]

    read_result = runner.invoke(
        cli_app,
        ["tool", "read-note", permalink, "--include-frontmatter"],
    )
    assert read_result.exit_code == 0, read_result.output
    read_data = json.loads(read_result.stdout)
    # Content frontmatter "session" wins over the --type "guide" flag.
    assert read_data["frontmatter"]["type"] == "session"
