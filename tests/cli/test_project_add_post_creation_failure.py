"""`bm project add` reports creation and indexing as separate outcomes (#1414 review).

The project row is durable before indexing starts. When indexing then failed,
the surrounding handler printed "Error adding project" and exited 1 — so the
user was told the add failed and sent back to a command that now refuses with
"already exists", with no way out that the output named.
"""

from typing import Any

import pytest
from typer.testing import CliRunner

import basic_memory.cli.commands.project as project_cmd
from basic_memory.cli.main import app as cli_app

runner = CliRunner()

WIDE_TERMINAL_ENV = {"COLUMNS": "240", "LINES": "60"}


class _Created:
    """Stand-in for the API's create-project response."""

    message = "Project 'research' added successfully"


@pytest.fixture
def created_project(monkeypatch):
    """Make creation succeed without touching the API or the database."""

    def _run(coro: Any) -> Any:
        # The command passes coroutines; close them so no "never awaited" warning
        # escapes, and let each test's patched step decide what happens next.
        coro.close()
        return _Created()

    monkeypatch.setattr(project_cmd, "run_with_cleanup", _run)


def test_indexing_failure_keeps_the_project_and_names_the_working_remedy(
    tmp_path, monkeypatch, created_project
):
    """A failure after creation must not be reported as a failure to create."""

    def explode(_project: str) -> Any:
        raise RuntimeError("semantic indexing initialization failed")

    monkeypatch.setattr(project_cmd, "index_project_and_report_readiness", explode)

    result = runner.invoke(
        cli_app,
        ["project", "add", "research", str(tmp_path)],
        env=WIDE_TERMINAL_ENV,
    )

    assert result.exit_code == 1
    # Creation is still reported as having happened.
    assert "added successfully" in result.output
    # And the failure is attributed to the step that actually failed.
    assert "was created, but a follow-up step failed" in result.output
    assert "semantic indexing initialization failed" in result.output
    # The old, misleading line is gone.
    assert "Error adding project" not in result.output
    # The remedy printed is the one that works from here.
    assert "Do not re-run 'bm project add'" in result.output
    assert "bm project index research" in result.output


def test_a_creation_failure_still_reports_a_failure_to_add(tmp_path, monkeypatch):
    """The pre-persist path keeps its original message.

    Nothing was created, so "Error adding project" is the true statement and
    re-running the command is the right advice.
    """

    def explode(_coro: Any) -> Any:
        _coro.close()
        raise RuntimeError("could not reach the API")

    monkeypatch.setattr(project_cmd, "run_with_cleanup", explode)

    result = runner.invoke(
        cli_app,
        ["project", "add", "research", str(tmp_path)],
        env=WIDE_TERMINAL_ENV,
    )

    assert result.exit_code == 1
    assert "Error adding project: could not reach the API" in result.output
    assert "was created, but" not in result.output


def test_a_step_that_exits_on_its_own_still_gets_the_remedy(tmp_path, monkeypatch, created_project):
    """`run_project_index` reports its own error and raises typer.Exit.

    That path already printed a message, so only the state and the remedy are
    added — the error is not duplicated with an empty detail.
    """
    import typer

    def bail(_project: str) -> Any:
        raise typer.Exit(1)

    monkeypatch.setattr(project_cmd, "index_project_and_report_readiness", bail)

    result = runner.invoke(
        cli_app,
        ["project", "add", "research", str(tmp_path)],
        env=WIDE_TERMINAL_ENV,
    )

    assert result.exit_code == 1
    assert "was created, but a follow-up step failed" in result.output
    # No stray "failed: 1" from stringifying the Exit code.
    assert "follow-up step failed:" not in result.output
    assert "bm project index research" in result.output
