"""Every remedy `bm project add` prints must work for the project it printed it for.

Four separate reviews found a remedy that did not: wrong for cloud mode, wrong
for a missing config entry, wrong when the name needed quoting, and wrong for
Team workspaces. Each was fixed by choosing a better command for a case someone
had thought of, which is why a fifth kept arriving.

So this stops enumerating cases and covers the matrix instead: project mode x
failing step, for every combination the code can actually reach. For each one it
runs the printed remedy and asserts the command is accepted for that project --
not that the text looks right, which is what let the earlier versions ship.

`test_every_failure_branch_has_a_row` derives the step list from the source, so
a new failure branch added without a row here fails rather than going untested.
"""

import ast
import importlib
import json
import re
import shlex
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import basic_memory.cli.commands.project as project_cmd
from basic_memory.cli.main import app as cli_app
from basic_memory.schemas.cloud import WorkspaceInfo

# The package exposes a *function* named `project_sync`, which shadows the
# submodule, so the module object has to be imported explicitly.
project_sync_cmd = importlib.import_module("basic_memory.cli.commands.cloud.project_sync")

runner = CliRunner()

WIDE_TERMINAL_ENV = {"COLUMNS": "240", "LINES": "60"}
PROJECT = "research"

# --- The matrix ---

LOCAL = "local"
CLOUD_PERSONAL = "cloud-personal"
CLOUD_TEAM = "cloud-team"

INDEX = "indexing it"
CONFIG_SAVE = "saving its cloud routing to local config"
SYNC_DIR = "creating the local sync directory"

MODES = (LOCAL, CLOUD_PERSONAL, CLOUD_TEAM)
STEPS = (INDEX, CONFIG_SAVE, SYNC_DIR)

# Which (mode, step) pairs `add_project` can actually reach. The local indexing
# step sits under `not effective_cloud_mode` and the two cloud steps under its
# negation, so the unreachable half is a property of that if/else -- asserted
# below rather than skipped, so it fails if the branching ever changes.
REACHABLE = {
    (LOCAL, INDEX),
    (CLOUD_PERSONAL, CONFIG_SAVE),
    (CLOUD_PERSONAL, SYNC_DIR),
    (CLOUD_TEAM, CONFIG_SAVE),
    (CLOUD_TEAM, SYNC_DIR),
}


def flat(output: str) -> str:
    """Collapse Rich's wrapping so assertions describe the message, not the width."""
    return " ".join(output.split())


def _reset_config_cache() -> None:
    """Forget the cached config, the way a fresh CLI process would."""
    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None


def _persisted_projects(config_file: Path) -> dict[str, Any]:
    if not config_file.exists():
        return {}
    return json.loads(config_file.read_text(encoding="utf-8")).get("projects", {})


def _printed_remedy_command(output: str) -> list[str]:
    """Pull the first runnable `bm ...` the output told the user to run.

    Bounded at a comma or a sentence-ending period, because one sentence can
    name two commands ("Index it with X, then check with Y"). The negative
    lookbehind drops the prose mention of `'bm project add'` in the "do not
    re-run" line, which is quoted; a real printed command is not.
    """
    candidates = re.findall(
        r"(?<!')bm ((?:project|cloud|status) [^,]*?)(?:,|\.\s|\.$|$)", flat(output)
    )
    runnable = [candidate.strip() for candidate in candidates if candidate.strip()]
    assert runnable, f"no runnable remedy command in output: {output}"
    # shlex.split, not str.split: the printed command is shell-quoted, so a name
    # with a space has to come back as one argument.
    return shlex.split(runnable[0])


# --- Source-derived guard ---


def test_every_failure_branch_has_a_row():
    """The table must cover every post-creation failure the command can report.

    Derived from the source so adding a branch without a row here is itself a
    failure, rather than silently leaving a remedy untested -- which is how each
    of the previous four shipped.
    """
    source = Path(project_cmd.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    reported_steps: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "_abort_after_project_created"):
            continue
        step_arg = next((kw.value for kw in node.keywords if kw.arg == "step"), None)
        assert step_arg is not None, "every abort must name the step that failed"
        # Steps are either a literal or an f-string whose first part is literal.
        if isinstance(step_arg, ast.Constant):
            reported_steps.add(str(step_arg.value))
        elif isinstance(step_arg, ast.JoinedStr):
            first = step_arg.values[0]
            assert isinstance(first, ast.Constant)
            reported_steps.add(str(first.value).strip())

    assert reported_steps == {INDEX, CONFIG_SAVE, SYNC_DIR}, (
        f"post-creation failure branches changed: {reported_steps}. "
        "Add a row to STEPS/REACHABLE and a remedy assertion for the new one."
    )


# --- Harness ---


_REAL_CONFIG_MANAGER = project_cmd.ConfigManager

# Paths whose creation is currently refused. Emptied when the test performs the
# repair the message asked for, so the remedy runs against a fixed system.
_REFUSED_MKDIRS: set[Path] = set()


class _Created:
    message = f"Project '{PROJECT}' added successfully"


class _Resolved:
    path = "/app/data/research"


@pytest.fixture(autouse=True)
def _no_refused_mkdirs():
    """Keep one test's refusal from leaking into the next."""
    _REFUSED_MKDIRS.clear()
    yield
    _REFUSED_MKDIRS.clear()


@pytest.fixture
def cloud_credentials(monkeypatch):
    monkeypatch.setattr(project_cmd, "_require_cloud_credentials", lambda _config: None)
    monkeypatch.setattr(project_cmd, "_resolve_workspace_id", lambda _config, _ws: "ws-123")


@pytest.fixture
def cloud_transfer(monkeypatch):
    """Let the cloud transfer commands run their guards, then stop before the network.

    The question each row asks is whether the printed command is *accepted* for
    this project -- a Team workspace refuses `bisync` before doing any work -- so
    the workspace guard stays real and only the transfer is stubbed.
    """
    transfers: list[str] = []

    def _transfer(name: str, *args: Any, **kwargs: Any) -> None:
        transfers.append(name)

    monkeypatch.setattr(project_sync_cmd, "_run_directional_transfer", _transfer)
    return transfers


@pytest.fixture
def workspace_type(request, monkeypatch):
    """Make the cloud commands see a personal or an organization workspace."""
    kind = "personal" if request.param == CLOUD_PERSONAL else "organization"

    async def _workspace(_name: str, _config: Any) -> WorkspaceInfo:
        return WorkspaceInfo(
            tenant_id="ws-123",
            workspace_type=kind,
            slug="ws",
            name="Workspace",
            role="owner",
            is_default=True,
        )

    monkeypatch.setattr(project_sync_cmd, "_get_workspace_for_project", _workspace)
    return kind


def _run_add(mode: str, failing_step: str, tmp_path: Path, monkeypatch) -> Any:
    """Invoke `project add` for one mode with one post-creation step failing."""
    created: list[str] = []

    def _run(coro: Any) -> Any:
        step = coro.__name__
        coro.close()
        if step == "_add_project":
            created.append(PROJECT)
            return _Created()
        if step == "_resolve":  # pragma: no cover - creation succeeds here
            return _Resolved()
        raise AssertionError(f"unexpected step: {step}")

    monkeypatch.setattr(project_cmd, "run_with_cleanup", _run)

    if failing_step == INDEX:

        def explode(_project: str) -> Any:
            raise RuntimeError("boom")

        monkeypatch.setattr(project_cmd, "index_project_and_report_readiness", explode)

    if failing_step == CONFIG_SAVE:
        real_config_manager = project_cmd.ConfigManager

        class _FailingSave:
            def __init__(self) -> None:
                self._real = real_config_manager()
                self.config = self._real.config
                self.config_file = self._real.config_file

            def save_config(self, _config: Any) -> None:
                raise PermissionError("read-only file system")

        monkeypatch.setattr(project_cmd, "ConfigManager", _FailingSave)

    local_path = tmp_path / "sync"
    if failing_step == SYNC_DIR:
        # A permissions failure, which is what this looks like in practice. An
        # unusable *path* would also make the config entry saved just before it
        # unloadable, which would confuse this test with a different problem.
        real_mkdir = Path.mkdir
        _REFUSED_MKDIRS.add(local_path)

        def _refuse(self: Path, *args: Any, **kwargs: Any) -> None:
            if self in _REFUSED_MKDIRS:
                raise PermissionError("permission denied")
            real_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", _refuse)

    args = ["project", "add", PROJECT]
    if mode == LOCAL:
        args.append(str(tmp_path / "notes"))
    else:
        args += ["--cloud", "--local-path", str(local_path)]
    return runner.invoke(cli_app, args, env=WIDE_TERMINAL_ENV)


def _stub_remedy_collaborators(monkeypatch, project_sync_module, workspace_kind: str) -> None:
    """Let each printed remedy reach its own guards without real IO.

    Every patch here overrides the one the failing step installed, which is how
    the repair is expressed: a later `setattr` wins, so the config write that
    failed now succeeds.
    """
    monkeypatch.setattr(project_cmd, "ConfigManager", _REAL_CONFIG_MANAGER)
    # "Create it yourself (or fix its permissions)" -- done.
    _REFUSED_MKDIRS.clear()
    # `bm project index` -> the shared reindex implementation.
    monkeypatch.setattr(project_cmd, "run_reindex_command", lambda **_kwargs: None)

    # `bm project add` -> creation refused because the remote project exists, so
    # the adoption path runs; that is the recovery the config-save remedy names.
    def _run(coro: Any) -> Any:
        step = coro.__name__
        coro.close()
        if step == "_add_project":
            raise RuntimeError(f"Project '{PROJECT}' already exists with different path")
        if step == "_resolve":
            return _Resolved()
        raise AssertionError(f"unexpected step: {step}")

    monkeypatch.setattr(project_cmd, "run_with_cleanup", _run)
    monkeypatch.setattr(project_cmd, "_require_cloud_credentials", lambda _config: None)
    monkeypatch.setattr(project_cmd, "_resolve_workspace_id", lambda _config, _ws: "ws-123")

    # `bm cloud pull` / `push` -> real workspace guard, stubbed transfer.
    async def _workspace(_name: str, _config: Any) -> WorkspaceInfo:
        return WorkspaceInfo(
            tenant_id="ws-123",
            workspace_type=workspace_kind,
            slug="ws",
            name="Workspace",
            role="owner",
            is_default=True,
        )

    monkeypatch.setattr(project_sync_module, "_get_workspace_for_project", _workspace)
    monkeypatch.setattr(project_sync_module, "_require_cloud_credentials", lambda _config: None)
    monkeypatch.setattr(
        project_sync_module, "_run_directional_transfer", lambda *args, **kwargs: None
    )


# --- The matrix test ---


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("step", STEPS)
def test_unreachable_combinations_stay_unreachable(
    mode, step, tmp_path, monkeypatch, cloud_credentials
):
    """A combination the branching cannot produce must not produce it.

    Asserted rather than skipped, so it fails the day it becomes reachable and
    needs a remedy of its own.
    """
    if (mode, step) in REACHABLE:
        pytest.skip("covered by test_the_printed_remedy_actually_works")

    result = _run_add(mode, step, tmp_path, monkeypatch)

    assert f"but {step}" not in flat(result.output), (
        f"({mode}, {step}) is now reachable; add it to REACHABLE so its remedy is tested"
    )


@pytest.mark.parametrize(
    ("mode", "workspace_type", "step"),
    [pytest.param(mode, mode, step, id=f"{mode}-{step}") for mode, step in sorted(REACHABLE)],
    indirect=["workspace_type"],
)
def test_the_printed_remedy_actually_works(
    mode, workspace_type, step, tmp_path, monkeypatch, cloud_credentials, cloud_transfer
):
    """Run what the output printed and assert the project accepts it.

    This is the assertion the four earlier remedies failed: each printed a
    command that reads sensibly and is refused for the project it was printed
    for -- by mode, by state, by argument splitting, or by workspace type.
    """
    result = _run_add(mode, step, tmp_path, monkeypatch)

    assert result.exit_code == 1
    assert f"but {step}" in flat(result.output)
    assert "Do not re-run 'bm project add'" in flat(result.output)

    remedy = _printed_remedy_command(result.output)

    # Repair whatever made the step fail, the way the message tells the user to,
    # so the remedy runs against a fixed system rather than a broken one. Only
    # this test's own patches are replaced -- `monkeypatch.undo()` would also
    # revert the autouse `isolated_home`, pointing the remedy at the developer's
    # real database.
    _reset_config_cache()
    _stub_remedy_collaborators(monkeypatch, project_sync_cmd, workspace_type)

    recovery = runner.invoke(cli_app, remedy, env=WIDE_TERMINAL_ENV)

    assert recovery.exit_code == 0, (
        f"{mode}/{step} printed a remedy that fails when run: "
        f"{shlex.join(remedy)}\n{recovery.output}"
    )


def test_a_team_workspace_really_does_refuse_the_personal_only_mirror(
    monkeypatch, cloud_credentials, cloud_transfer
):
    """Control for the Team rows: the command we stopped advising does dead-end.

    Without this, the Team rows above would pass even if `bisync` were harmless
    there, and would not be evidence of anything.
    """

    async def _team(_name: str, _config: Any) -> WorkspaceInfo:
        return WorkspaceInfo(
            tenant_id="ws-123",
            workspace_type="organization",
            slug="ws",
            name="Workspace",
            role="owner",
            is_default=True,
        )

    monkeypatch.setattr(project_sync_cmd, "_get_workspace_for_project", _team)
    monkeypatch.setattr(project_sync_cmd, "_require_cloud_credentials", lambda _config: None)

    result = runner.invoke(cli_app, ["cloud", "bisync", "--name", PROJECT], env=WIDE_TERMINAL_ENV)

    assert result.exit_code == 1
    assert "only supported on Personal workspaces" in flat(result.output)


# --- Convergence ---
#
# "Exits 0" is not enough: a command can succeed forever and never finish the
# job. `bm project index` passed `--full`, which clears every project vector
# before the sync runs, so a note with more chunks than one shard had its
# completed shard deleted and was deferred again on every run -- each run left
# the user further from IDLE than before (#1440 review). The earlier `--search`
# choice failed the same property from the other side, leaving embeddings
# unbuilt. So the remedy has to be run until readiness settles, not once.

MAX_REMEDY_ATTEMPTS = 5


def test_project_index_does_not_force_a_full_rebuild():
    """The alias must not clear vectors it is meant to complete.

    `_reindex` clears every project vector when `full` is set, which throws away
    the shard the previous run finished. Asserted on the call rather than by
    running a 256-chunk note, because the destruction is unconditional and the
    flag is the whole mechanism.
    """
    calls: list[dict[str, Any]] = []

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(project_cmd, "run_reindex_command", lambda **kwargs: calls.append(kwargs))
        result = runner.invoke(cli_app, ["project", "index", PROJECT], env=WIDE_TERMINAL_ENV)

    assert result.exit_code == 0, result.output
    assert calls == [{"embeddings": True, "search": True, "full": False, "project": PROJECT}], (
        "the advertised remedy must be incremental, or repeated runs cannot converge"
    )


def test_repeating_the_remedy_converges_on_a_partially_embedded_project():
    """Run the remedy until readiness settles, with a bounded number of attempts.

    A sharded entity needs one run per shard, so the property is not "one run
    finishes it" but "repeated runs get there". A remedy that discards progress
    never terminates this loop, which is exactly what `--full` did.
    """
    remaining_shards = {"count": 3}
    attempts = 0

    def fake_reindex(**kwargs: Any) -> None:
        # Models the sharding contract: an incremental pass finishes one more
        # shard, a full pass throws the finished ones away first.
        if kwargs["full"]:
            remaining_shards["count"] = 3
        remaining_shards["count"] = max(0, remaining_shards["count"] - 1)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(project_cmd, "run_reindex_command", fake_reindex)
        while remaining_shards["count"] > 0 and attempts < MAX_REMEDY_ATTEMPTS:
            attempts += 1
            result = runner.invoke(cli_app, ["project", "index", PROJECT], env=WIDE_TERMINAL_ENV)
            assert result.exit_code == 0, result.output

    assert remaining_shards["count"] == 0, (
        f"the remedy did not converge in {MAX_REMEDY_ATTEMPTS} runs; "
        "a command that succeeds without finishing the job is not a remedy"
    )
