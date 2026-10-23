"""Tests for `bm man apropos` and `bm man <topic>` misses: the bundled manual only.

Both read the pages shipped in the package, so they need no project, database or
network. A topic that is no bundled page is a miss.
"""

import json

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


def _flattened(output: str) -> str:
    return " ".join(output.split())


# --- apropos ---


def test_apropos_lists_matching_pages():
    result = runner.invoke(cli_app, ["man", "apropos", "frontmatter filters"])

    assert result.exit_code == 0, result.output
    assert any(line.startswith("bm-find(3) ") for line in result.output.splitlines())


def test_apropos_json_carries_page_summary_and_tool():
    result = runner.invoke(cli_app, ["man", "apropos", "frontmatter filters", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    by_page = {row["page"]: row for row in payload["results"]}
    assert payload["query"] == "frontmatter filters"
    assert by_page["bm-find(3)"]["tool"] == "bm_find"
    assert by_page["bm-find(3)"]["summary"]


def test_apropos_with_no_match_is_a_successful_search():
    result = runner.invoke(cli_app, ["man", "apropos", "zzz-no-such-word"])

    assert result.exit_code == 0
    assert "No manual pages mention 'zzz-no-such-word'" in result.output


# --- show ---


def test_show_miss_points_at_the_index():
    result = runner.invoke(cli_app, ["man", "docs/cloud-sync"])

    assert result.exit_code == 1
    assert "No manual entry for docs/cloud-sync" in _flattened(result.output)
    assert "bm man list" in _flattened(result.output)


def test_show_reads_a_bm_tool_page_by_tool_name():
    result = runner.invoke(cli_app, ["man", "bm_search"])

    assert result.exit_code == 0, result.output
    assert result.output.startswith("# bm-search(3)\n")
