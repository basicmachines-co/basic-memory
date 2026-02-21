"""Integration coverage for tool search-notes with metadata filters."""

import json

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


def test_search_notes_query_plus_meta_filter(app, app_config, test_project, config_manager):
    """`bm tool search-notes` should support query + metadata filter together."""
    active_content = "---\nstatus: active\n---\n# Active Meta Note\n\nMetaFilterToken"
    inactive_content = "---\nstatus: inactive\n---\n# Inactive Meta Note\n\nMetaFilterToken"

    active_write = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Active Meta Note",
            "--folder",
            "meta-tests",
            "--content",
            active_content,
        ],
    )
    assert active_write.exit_code == 0, active_write.output
    active_data = json.loads(active_write.stdout)

    inactive_write = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Inactive Meta Note",
            "--folder",
            "meta-tests",
            "--content",
            inactive_content,
        ],
    )
    assert inactive_write.exit_code == 0, inactive_write.output
    inactive_data = json.loads(inactive_write.stdout)

    search = runner.invoke(
        cli_app,
        [
            "tool",
            "search-notes",
            "MetaFilterToken",
            "--meta",
            "status=active",
            "--local",
            "--page-size",
            "20",
        ],
    )
    assert search.exit_code == 0, search.output

    payload = json.loads(search.stdout)
    permalinks = {item["permalink"] for item in payload["results"]}
    assert active_data["permalink"] in permalinks
    assert inactive_data["permalink"] not in permalinks
