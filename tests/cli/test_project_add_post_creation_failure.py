"""`bm project add` reports creation and its follow-up steps separately (#1414 review).

The project row is durable before the follow-up work starts. Two things went
wrong there in turn:

1. A follow-up failure was reported as "Error adding project", so the user was
   told the add failed and sent back to a command that now refuses with
   "already exists".
2. Once that was split out, every follow-up shared one remedy — `bm project
   index` — which the local-only reindex path refuses outright for a cloud
   project, and which could not repair a failed config write or mkdir anyway.

So the remedy has to follow the step that failed and the project's mode.
"""

from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

import basic_memory.cli.commands.project as project_cmd
from basic_memory.cli.main import app as cli_app

runner = CliRunner()

WIDE_TERMINAL_ENV = {"COLUMNS": "240", "LINES": "60"}


def flat(output: str) -> str:
    """Collapse Rich's line wrapping so assertions describe the message, not the width.

    The module-level Console fixes its width at import, so `env=COLUMNS` on the
    runner cannot widen it and a long remedy wraps mid-sentence.
    """
    return " ".join(output.split())


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


@pytest.fixture
def cloud_add(monkeypatch, created_project):
    """Let a `--cloud` add reach its post-creation steps without real credentials."""
    monkeypatch.setattr(project_cmd, "_require_cloud_credentials", lambda _config: None)
    monkeypatch.setattr(project_cmd, "_resolve_workspace_id", lambda _config, _ws: "ws-123")


# --- Local projects ---


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
    assert "added successfully" in flat(result.output)
    # The failure is attributed to the step that actually failed.
    assert "was created, but indexing it failed" in flat(result.output)
    assert "semantic indexing initialization failed" in flat(result.output)
    # The old, misleading line is gone.
    assert "Error adding project" not in flat(result.output)
    # A local project keeps the remedy that fits it.
    assert "Do not re-run 'bm project add'" in flat(result.output)
    assert "bm project index research" in flat(result.output)


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
    assert "Error adding project: could not reach the API" in flat(result.output)
    assert "was created, but" not in flat(result.output)


def test_a_step_that_exits_on_its_own_still_gets_the_remedy(tmp_path, monkeypatch, created_project):
    """`run_project_index` reports its own error and raises typer.Exit.

    That path already printed a message, so only the state and the remedy are
    added — the error is not duplicated with an empty detail.
    """

    def bail(_project: str) -> Any:
        raise typer.Exit(1)

    monkeypatch.setattr(project_cmd, "index_project_and_report_readiness", bail)

    result = runner.invoke(
        cli_app,
        ["project", "add", "research", str(tmp_path)],
        env=WIDE_TERMINAL_ENV,
    )

    assert result.exit_code == 1
    assert "was created, but indexing it failed" in flat(result.output)
    # No stray "failed: 1" from stringifying the Exit code.
    assert "indexing it failed:" not in flat(result.output)
    assert "bm project index research" in flat(result.output)


# --- Cloud projects ---


def test_a_failed_cloud_config_save_gets_a_cloud_remedy(tmp_path, monkeypatch, cloud_add):
    """`bm project index` is refused for cloud projects, so it must not be advised.

    The remote project exists; only this machine's routing entry is missing, so
    recovery is local config plus `set-cloud` — not an index pass that the
    local-only reindex path would reject for this project outright.
    """
    real_config_manager = project_cmd.ConfigManager

    class _FailingSave:
        def __init__(self) -> None:
            self._real = real_config_manager()
            self.config = self._real.config
            self.config_file = Path("/etc/basic-memory/config.json")

        def save_config(self, _config: Any) -> None:
            raise PermissionError("read-only file system")

    monkeypatch.setattr(project_cmd, "ConfigManager", _FailingSave)

    result = runner.invoke(
        cli_app,
        ["project", "add", "research", "--cloud", "--local-path", str(tmp_path / "sync")],
        env=WIDE_TERMINAL_ENV,
    )

    assert result.exit_code == 1
    assert "added successfully" in flat(result.output)
    assert "was created, but saving its cloud routing to local config failed" in flat(result.output)
    assert "read-only file system" in flat(result.output)
    # The regression: a cloud project must never be sent to the local-only reindex.
    assert "bm project index" not in flat(result.output)
    # It gets a remedy that is valid for its mode, naming the file that failed.
    assert "/etc/basic-memory/config.json" in flat(result.output)
    assert "bm project set-cloud research --workspace ws-123" in flat(result.output)
    assert "Do not re-run 'bm project add'" in flat(result.output)


def test_a_failed_local_sync_mkdir_gets_a_sync_remedy(tmp_path, monkeypatch, cloud_add):
    """A directory that cannot be created is repaired on the filesystem, then resynced."""
    # A real mkdir failure: the parent is a file, so creating a child raises.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    local_path = blocker / "sync"

    result = runner.invoke(
        cli_app,
        ["project", "add", "research", "--cloud", "--local-path", str(local_path)],
        env=WIDE_TERMINAL_ENV,
    )

    assert result.exit_code == 1
    assert "added successfully" in flat(result.output)
    assert "was created, but creating the local sync directory" in flat(result.output)
    assert "bm project index" not in flat(result.output)
    assert "bm cloud bisync --name research --resync" in flat(result.output)
    assert "Do not re-run 'bm project add'" in flat(result.output)
