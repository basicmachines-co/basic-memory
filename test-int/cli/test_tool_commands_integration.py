"""`bm tool` against a real project: each command calls its bm_* tool end to end."""

import json
from typing import Any

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app


def bm_tool(*arguments: str, stdin: str | None = None) -> dict[str, Any]:
    """Run a `bm tool` command with --json and return its parsed result."""
    result = CliRunner().invoke(cli_app, ["tool", *arguments, "--json"], input=stdin)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_note_lifecycle(app, test_project, config_manager) -> None:
    project = ["--project", test_project.name]

    written = bm_tool(
        "write", "--identifier", "plans/q4.md", "--note-type", "plan", *project,
        stdin="# Q4\n\n## Status\n\nDraft.\n",
    )  # fmt: skip
    assert written["action"] == "created"

    edited = bm_tool(
        "edit", "plans/q4.md", "--command", "replace_section", "--section", "Status",
        "--content", "Shipped.", "--expected-checksum", written["checksum"], *project,
    )  # fmt: skip
    assert edited["checksum"] != written["checksum"]

    read = bm_tool("read", "plans/q4.md", "--section", "Status", *project)
    assert "Shipped." in read["content"]

    found = bm_tool("search", "Shipped", "--filters", '{"type": "plan"}', *project)
    assert [row["file_path"] for row in found["results"]] == ["plans/q4.md"]

    bm_tool("move", "--identifier", "plans/q4.md", "--destination", "archive/q4.md", *project)
    listed = bm_tool("list", "archive", *project)
    assert "archive/q4.md" in json.dumps(listed)

    bm_tool("delete", "--identifier", "archive/q4.md", *project)
    missing = CliRunner().invoke(cli_app, ["tool", "read", "archive/q4.md", *project])
    assert missing.exit_code == 1
    assert "Error:" in missing.stderr


def test_text_output_is_the_tool_text(app, test_project, config_manager) -> None:
    bm_tool("write", "--identifier", "a.md", "--content", "alpha", "--project", test_project.name)

    result = CliRunner().invoke(
        cli_app, ["tool", "search", "alpha", "--project", test_project.name]
    )

    assert result.exit_code == 0, result.output
    assert "a.md" in result.stdout
    assert not result.stdout.lstrip().startswith("{")


def test_invalid_argument_names_the_flag(app, test_project, config_manager) -> None:
    result = CliRunner().invoke(
        cli_app, ["tool", "search", "x", "--mode", "bogus", "--project", test_project.name]
    )

    assert result.exit_code == 1
    assert "--mode" in result.stderr


def test_missing_default_project_asks_for_one(app, test_project, config_manager) -> None:
    config = config_manager.load_config()
    config.default_project = None
    config_manager.save_config(config)

    result = CliRunner().invoke(cli_app, ["tool", "read", "a.md"])

    assert result.exit_code == 1
    assert "pass --project" in result.stderr
