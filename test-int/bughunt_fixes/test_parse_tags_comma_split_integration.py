"""Bug: CLI `write-note --tags "a,b"` does NOT split the comma string, but the
MCP write_note(tags="a,b") DOES (parse_tags splits a bare string but treats each
list element as a single literal tag).

Typer collects a single --tags value into a one-element list ['a,b'], and
parse_tags(['a,b']) returns ['a,b'] (no per-element comma split). The MCP tool
receives the bare string 'a,b' and parse_tags('a,b') returns ['a','b'].

Result: the SAME comma-string input yields different tags on CLI vs MCP, even
though write_note's docstring promises comma-separated-string support.
"""

import json
import pytest
from fastmcp import Client
from typer.testing import CliRunner
from basic_memory.cli.main import app as cli_app

runner = CliRunner()


def test_cli_write_note_comma_tags_split_matches_mcp(app, app_config, test_project, config_manager):
    # CLI: single --tags value containing a comma
    write = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "CLI Comma Split",
            "--folder",
            "cli-comma-split",
            "--content",
            "# CLI Comma Split\n\nbody",
            "--tags",
            "alpha,beta",
        ],
    )
    assert write.exit_code == 0, write.output
    permalink = json.loads(write.stdout)["permalink"]

    read = runner.invoke(
        cli_app,
        ["tool", "read-note", permalink, "--include-frontmatter", "--local"],
    )
    assert read.exit_code == 0, read.output
    content = json.loads(read.stdout)["content"]

    # Correct behavior: two distinct tags (matching MCP write_note semantics).
    assert "- alpha\n" in content and "- beta\n" in content, (
        "CLI --tags 'alpha,beta' should split into two tags like MCP write_note does; "
        f"got frontmatter:\n{content}"
    )
    assert "alpha,beta" not in content, "comma string must not survive as a single literal tag"


@pytest.mark.asyncio
async def test_mcp_write_note_comma_tags_split_baseline(mcp_server, app, test_project):
    """Baseline: MCP write_note DOES split comma strings (the behavior CLI should match)."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "MCP Comma Split",
                "directory": "mcp-comma-split",
                "content": "# MCP Comma Split\n\nbody",
                "tags": "alpha,beta",
            },
        )
        read = await client.call_tool(
            "read_note",
            {"project": test_project.name, "identifier": "MCP Comma Split"},
        )
        text = read.content[0].text
        assert "- alpha\n" in text and "- beta\n" in text, (
            f"MCP write_note should split comma string into two tags; got:\n{text}"
        )
