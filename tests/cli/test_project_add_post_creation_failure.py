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

import json
import re
import shlex
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

import basic_memory.cli.commands.project as project_cmd
from basic_memory.cli.main import app as cli_app
from basic_memory.config import ProjectMode

runner = CliRunner()

WIDE_TERMINAL_ENV = {"COLUMNS": "240", "LINES": "60"}


def _reset_config_cache() -> None:
    """Forget the cached config, the way a fresh CLI process would."""
    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None


def _persisted_projects(config_file: Path) -> dict[str, Any]:
    """Read projects straight from the config file, ignoring any in-memory state."""
    if not config_file.exists():
        return {}
    return json.loads(config_file.read_text(encoding="utf-8")).get("projects", {})


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


class _Resolved:
    """Stand-in for a resolve of a project that already exists remotely."""

    path = "/app/data/research"


@pytest.fixture
def remote_projects(monkeypatch, cloud_add):
    """A fake remote store, so `add` can be run twice and see its own first run.

    The command creates a distinctly named coroutine per step, which is what
    lets one stub serve both the create call and the existence probe.
    """
    created: set[str] = set()

    def _run(coro: Any) -> Any:
        step = coro.__name__
        coro.close()
        if step == "_add_project":
            if "research" in created:
                # What a server says when the project is already there. The
                # command must not depend on this wording.
                raise RuntimeError(
                    "Project 'research' already exists with different path. "
                    "Existing: /app/data/research, Requested: research"
                )
            created.add("research")
            return _Created()
        if step == "_resolve":
            if "research" in created:
                return _Resolved()
            raise RuntimeError("Project not found")
        raise AssertionError(f"unexpected step: {step}")

    monkeypatch.setattr(project_cmd, "run_with_cleanup", _run)
    return created


def _printed_remedy_command(output: str) -> list[str]:
    """Pull the runnable `bm ...` the output told the user to run.

    The test runs what was printed rather than a command it chose itself; a
    remedy that only looks right is exactly what shipped twice before. The
    prose also names `'bm project add'` in quotes when telling the user not to
    re-run it blindly, so only a candidate carrying flags is a command.
    """
    candidates = re.findall(r"bm ((?:project|cloud) [^.]+?)(?:\.\s|\.$|$)", flat(output))
    runnable = [candidate.strip() for candidate in candidates if "--" in candidate]
    assert runnable, f"no runnable remedy command in output: {output}"
    # shlex.split, not str.split: the printed command is shell-quoted, and a name
    # with a space must come back as one argument -- which is the whole point.
    return shlex.split(runnable[0])


def test_a_failed_cloud_config_save_prints_a_remedy_that_actually_works(
    tmp_path, monkeypatch, remote_projects
):
    """The recovery must be a command that succeeds, not a better-worded dead end.

    `save_config` failing on a previously absent project used to leave a state
    with no way out: `set-cloud` refuses a name missing from config, and
    re-running `add` refused because the remote project existed. So this test
    runs the printed remedy and asserts it recovers.
    """
    real_config_manager = project_cmd.ConfigManager
    writes_fail = {"now": True}

    class _MaybeFailingSave:
        def __init__(self) -> None:
            self._real = real_config_manager()
            self.config = self._real.config
            self.config_file = self._real.config_file

        def save_config(self, config: Any) -> None:
            if writes_fail["now"]:
                raise PermissionError("read-only file system")
            self._real.save_config(config)

    monkeypatch.setattr(project_cmd, "ConfigManager", _MaybeFailingSave)

    first = runner.invoke(cli_app, ["project", "add", "research", "--cloud"], env=WIDE_TERMINAL_ENV)

    assert first.exit_code == 1
    assert "was created, but saving its cloud routing to local config failed" in flat(first.output)
    # The remote project exists now, and nothing was persisted for it here.
    assert "research" in remote_projects
    assert "research" not in _persisted_projects(real_config_manager().config_file)
    # The command that cannot work in this state must not be advised.
    assert "bm project set-cloud" not in flat(first.output)

    # The user fixes what the message told them to fix...
    writes_fail["now"] = False
    # ...and runs the command again, which in real use is a new process. Drop the
    # config cache so the retry re-reads the file, as that process would: a failed
    # save leaves its in-memory mutation behind, and the retry must not see it.
    _reset_config_cache()

    # ...and runs exactly what the output printed.
    remedy = _printed_remedy_command(first.output)
    assert remedy[:3] == ["project", "add", "research"]
    second = runner.invoke(cli_app, remedy, env=WIDE_TERMINAL_ENV)

    assert second.exit_code == 0, second.output
    # It adopted the existing project rather than failing on it or creating a second.
    assert "already exists" in flat(second.output)
    assert "adopting it into this machine's config" in flat(second.output)

    _reset_config_cache()
    persisted = _persisted_projects(real_config_manager().config_file)
    assert "research" in persisted
    assert persisted["research"]["mode"] == ProjectMode.CLOUD.value


def test_a_genuinely_configured_project_is_not_adopted_over(tmp_path, monkeypatch, cloud_add):
    """Adoption is only for "remote exists, config missing".

    A name already in this machine's config is genuinely added, so a creation
    failure there is a real conflict — a local re-add under a different path,
    say — and adopting would silently ignore the path the caller asked for.
    """
    resolved: list[str] = []

    def _run(coro: Any) -> Any:
        step = coro.__name__
        coro.close()
        if step == "_resolve":  # pragma: no cover - must never be reached
            resolved.append("probed")
            return _Resolved()
        raise RuntimeError("Project 'test-project' already exists with different path")

    monkeypatch.setattr(project_cmd, "run_with_cleanup", _run)
    existing_name = next(iter(project_cmd.ConfigManager().config.projects))

    result = runner.invoke(
        cli_app, ["project", "add", existing_name, str(tmp_path)], env=WIDE_TERMINAL_ENV
    )

    assert result.exit_code == 1
    assert "Error adding project" in flat(result.output)
    assert "adopting" not in flat(result.output)
    # The probe is not even attempted: config already answers the question.
    assert resolved == []


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


def test_the_remedy_survives_a_project_name_with_a_space(tmp_path, monkeypatch, cloud_add):
    """A name with a space must round-trip through the printed command.

    Unquoted, `bm project add My Notes --cloud` parses as project "My" with
    positional path "Notes" -- so the remedy runs, succeeds, and acts on a
    project the user never named. With adoption in place it could adopt the
    wrong one, which is why this asserts on what the command *did*, not on the
    text that was printed.
    """
    created: list[str] = []
    adopted: list[str] = []

    def _run(coro: Any) -> Any:
        step = coro.__name__
        coro.close()
        if step == "_add_project":
            if "My Notes" in created:
                raise RuntimeError("Project 'My Notes' already exists with different path")
            created.append("My Notes")
            return _Created()
        if step == "_resolve":
            adopted.append("My Notes")
            return _Resolved()
        raise AssertionError(f"unexpected step: {step}")

    monkeypatch.setattr(project_cmd, "run_with_cleanup", _run)

    real_config_manager = project_cmd.ConfigManager
    writes_fail = {"now": True}

    class _MaybeFailingSave:
        def __init__(self) -> None:
            self._real = real_config_manager()
            self.config = self._real.config
            self.config_file = self._real.config_file

        def save_config(self, config: Any) -> None:
            if writes_fail["now"]:
                raise PermissionError("read-only file system")
            self._real.save_config(config)

    monkeypatch.setattr(project_cmd, "ConfigManager", _MaybeFailingSave)

    first = runner.invoke(cli_app, ["project", "add", "My Notes", "--cloud"], env=WIDE_TERMINAL_ENV)
    assert first.exit_code == 1

    writes_fail["now"] = False
    _reset_config_cache()

    remedy = _printed_remedy_command(first.output)
    # The name survived as a single argument rather than splitting into two.
    assert remedy == ["project", "add", "My Notes", "--cloud", "--workspace", "ws-123"]

    second = runner.invoke(cli_app, remedy, env=WIDE_TERMINAL_ENV)

    assert second.exit_code == 0, second.output
    # It adopted the project the user actually named, and created no second one.
    assert adopted == ["My Notes"]
    assert created == ["My Notes"]

    _reset_config_cache()
    persisted = _persisted_projects(real_config_manager().config_file)
    assert "My Notes" in persisted
    assert persisted["My Notes"]["mode"] == ProjectMode.CLOUD.value
