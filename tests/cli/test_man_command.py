"""Tests for `bm man` (#952 / #610): reading bundled pages and making `man bm` work."""

import re
import subprocess
from unittest.mock import AsyncMock, patch

import pytest
import typer.main
from fastmcp.exceptions import ToolError
from typer.core import TyperGroup
from typer.testing import CliRunner

from basic_memory.cli.app import app

# Importing main registers every command group, including the POSIX verbs the
# section-1 pages document.
from basic_memory.cli.main import app as full_app
from basic_memory.man import bundled_pages

# Importing the module registers the man command group on the top-level app.
import basic_memory.cli.commands.man as man_command  # noqa: F401

runner = CliRunner()


def _flattened(output: str) -> str:
    # rich wraps console output at the terminal width, which differs between
    # local shells and CI — collapse all whitespace so phrase assertions can't
    # be split by a line break.
    return " ".join(output.split())


@pytest.mark.parametrize(
    "argv",
    [
        ["man", "search-notes"],
        ["man", "search-notes(3)"],
        ["man", "search_notes"],
        ["man", "3/search-notes"],
        ["man", "show", "search-notes"],
    ],
)
def test_man_topic_prints_the_page_as_markdown(argv):
    """`bm man <topic>` reads like man(1): the topic needs no subcommand and any spelling works."""
    result = runner.invoke(app, argv)

    assert result.exit_code == 0, result.output
    assert result.output.startswith("# search-notes(3)\n")
    assert "## GOTCHAS" in result.output
    assert "title: search-notes(3)" not in result.output  # frontmatter is not rendered


# A bundled miss now falls through to the MCP man tool (manual-project notes,
# #1404); mock it so this test stays hermetic regardless of local config.
@patch(
    "basic_memory.mcp.tools.man",
    new_callable=AsyncMock,
    side_effect=ToolError("No manual entry for no-such-page"),
)
def test_man_unknown_topic_fails_and_points_at_list(mock_mcp_man):
    result = runner.invoke(app, ["man", "no-such-page"])

    assert result.exit_code == 1
    assert "No manual entry for no-such-page" in _flattened(result.output)
    assert "bm man list" in _flattened(result.output)


def test_man_list_is_apropos():
    result = runner.invoke(app, ["man", "list"])

    assert result.exit_code == 0, result.output
    for page in bundled_pages():
        assert page.title in result.output
        assert page.summary in result.output


def test_man_help_still_lists_subcommands():
    """A leading option is not a topic: `--help` reaches the group, not `show`."""
    result = runner.invoke(app, ["man", "--help"])

    assert result.exit_code == 0, result.output
    for command in ("install", "list", "show"):
        assert command in result.output


def test_man_install_writes_pages_to_target(tmp_path):
    """Install copies every bundled page into <root>/man1 as valid groff."""
    result = runner.invoke(app, ["man", "install", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.output

    bm_page = tmp_path / "man1" / "bm.1"
    alias_page = tmp_path / "man1" / "basic-memory.1"
    assert bm_page.exists()
    assert alias_page.exists()
    # groff sanity: a real title header and the alias .so include
    assert bm_page.read_text().startswith(".TH BM 1")
    assert alias_page.read_text().strip() == ".so man1/bm.1"
    assert "Try:" in _flattened(result.output)


def test_man_install_defaults_to_local_share_man(tmp_path, monkeypatch):
    """Without --dir, pages land under ~/.local/share/man (HOME is isolated here)."""

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("manpath")

    monkeypatch.setattr(man_command.subprocess, "run", fake_run)
    result = runner.invoke(app, ["man", "install"])

    assert result.exit_code == 0, result.output
    # The isolated_home fixture points HOME at tmp_path, so the default root
    # resolves inside the test sandbox.
    assert (tmp_path / ".local" / "share" / "man" / "man1" / "bm.1").exists()


def test_man_install_warns_when_root_not_on_manpath(tmp_path, monkeypatch):
    """A root provably absent from manpath output gets the MANPATH hint."""

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=["manpath"], returncode=0, stdout="/usr/share/man:/opt/man", stderr=""
        )

    monkeypatch.setattr(man_command.subprocess, "run", fake_run)
    result = runner.invoke(app, ["man", "install", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "not on your manpath" in _flattened(result.output)
    assert "MANPATH" in _flattened(result.output)


def test_man_install_stays_quiet_when_manpath_unavailable(tmp_path, monkeypatch):
    """No manpath binary → no false-alarm warning, install still succeeds."""

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("manpath")

    monkeypatch.setattr(man_command.subprocess, "run", fake_run)
    result = runner.invoke(app, ["man", "install", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "not on your manpath" not in _flattened(result.output)


def test_man_install_treats_manpath_failure_as_unknown(tmp_path, monkeypatch):
    """manpath exiting non-zero → unknown, no warning."""

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=["manpath"], returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(man_command.subprocess, "run", fake_run)
    result = runner.invoke(app, ["man", "install", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "not on your manpath" not in _flattened(result.output)


# Section 1 is the CLI's own documentation, so a page and its command must not
# drift: an option a user can type that the page never names is undocumented.
# find(1) went stale exactly this way when `bm find` grew --meta/--fields.
PAGES_WITHOUT_TOP_LEVEL_COMMANDS = {"apropos"}  # `bm man apropos`, not `bm apropos`
DOCUMENTED_VERBS = {"cat", "find", "grep", "head", "ls", "tail", "tree"}


def test_section_1_pages_document_every_option_of_their_command():
    """Every long option of a documented verb appears in its manual page."""
    # typer vendors its own click, so the group type comes from typer.core.
    cli = typer.main.get_command(full_app)
    assert isinstance(cli, TyperGroup)
    checked: set[str] = set()

    for page in bundled_pages():
        if page.section != 1 or page.name in PAGES_WITHOUT_TOP_LEVEL_COMMANDS:
            continue
        body = page.body()
        for param in cli.commands[page.name].params:
            for option in param.opts:
                if not option.startswith("--"):
                    continue
                # Boundary match so --page cannot stand in for --page-size.
                assert re.search(rf"{re.escape(option)}(?![\w-])", body), (
                    f"{page.title} does not document {option}; the page is stale"
                )
        checked.add(page.name)

    assert checked == DOCUMENTED_VERBS


def test_man_install_skips_app_initialization(tmp_path, monkeypatch):
    """man install must not touch the database (PR #971 review).

    Installing offline docs only copies packaged files; a locked or broken
    local database must not block it, so `man` is in skip_init_commands.
    """
    import basic_memory.services.initialization as init_module

    def explode(*args, **kwargs):
        raise AssertionError("ensure_initialization must not run for `bm man`")

    monkeypatch.setattr(init_module, "ensure_initialization", explode)
    result = runner.invoke(app, ["man", "install", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "man1" / "bm.1").exists()
