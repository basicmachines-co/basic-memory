"""Unit tests for `bm doctor` pieces that do not need a running app (#1798, #1027)."""

import subprocess
from pathlib import Path
from typing import Callable, NoReturn

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
import basic_memory.cli.commands.doctor as doctor_cmd
from basic_memory.index import note_content_materialization
from basic_memory.schemas.doctor import IndexProjectAction, ReindexSearchAction

runner = CliRunner()


def _raise(exc: Exception) -> Callable[..., NoReturn]:
    def raiser(*_args: object, **_kwargs: object) -> NoReturn:
        raise exc

    return raiser


# --- Failure output (#1027) ---


def test_doctor_failure_prints_error_message(monkeypatch):
    """A doctor failure must name the error instead of printing a bare prefix."""
    monkeypatch.setattr(doctor_cmd, "inspect_projects", _raise(ValueError("project missing")))
    result = runner.invoke(app, ["doctor", "--skip-self-test"])
    assert result.exit_code == 1
    assert "Doctor failed: project missing" in result.output


def test_doctor_failure_message_never_blank(monkeypatch):
    """str() of a message-less exception is empty; the output must fall back to repr."""
    monkeypatch.setattr(doctor_cmd, "inspect_projects", _raise(ValueError()))
    result = runner.invoke(app, ["doctor", "--skip-self-test"])
    assert result.exit_code == 1
    assert "Doctor failed: ValueError()" in result.output


def test_doctor_unexpected_failure_message_never_blank(monkeypatch):
    monkeypatch.setattr(doctor_cmd, "inspect_projects", _raise(RuntimeError()))
    result = runner.invoke(app, ["doctor", "--skip-self-test"])
    assert result.exit_code == 1
    assert "Doctor failed: RuntimeError()" in result.output


def test_cloud_doctor_is_refused_with_the_tracking_issue():
    result = runner.invoke(app, ["doctor", "--cloud"])
    assert result.exit_code == 1
    assert "cloud support is tracked in #1798" in " ".join(result.output.split())


# --- Actions become pasteable commands ---


@pytest.mark.parametrize(
    ("action", "command"),
    [
        (IndexProjectAction(), "bm project index 'My Notes'"),
        (ReindexSearchAction(full=False), "bm reindex --search --project 'My Notes'"),
        (ReindexSearchAction(full=True), "bm reindex --search --full --project 'My Notes'"),
    ],
)
def test_actions_render_as_quoted_commands(action, command):
    """A project name with a space must survive being pasted back into a shell."""
    assert doctor_cmd.action_command(action, "My Notes") == command


# --- Sandboxed self-test ---


def test_sandbox_environment_drops_every_basic_memory_setting(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BASIC_MEMORY_PROJECT_ROOT", "/real/root")
    monkeypatch.setenv("BASIC_MEMORY_CLOUD_MODE", "true")
    monkeypatch.setenv("PATH", "/usr/bin")

    environment = doctor_cmd.sandbox_environment(tmp_path, "/real/fastembed")

    assert "BASIC_MEMORY_PROJECT_ROOT" not in environment
    assert "BASIC_MEMORY_CLOUD_MODE" not in environment
    assert environment["PATH"] == "/usr/bin"
    assert environment["BASIC_MEMORY_CONFIG_DIR"] == str(tmp_path / "config")
    assert environment["BASIC_MEMORY_HOME"] == str(tmp_path / "home" / "basic-memory")
    assert environment["HOME"] == str(tmp_path / "home")
    assert environment["TMPDIR"] == environment["TEMP"] == environment["TMP"]
    assert Path(environment["TMPDIR"]).is_relative_to(tmp_path)
    # The model cache is shared so the sandbox never re-downloads it.
    assert environment["FASTEMBED_CACHE_PATH"] == "/real/fastembed"


type RecordedRun = tuple[list[str], dict[str, str]]


def _fake_run(returncode: int, stdout: str, calls: list[RecordedRun]):
    def run(argv: list[str], **kwargs):
        calls.append((argv, kwargs["env"]))
        sandbox_config = Path(kwargs["env"]["BASIC_MEMORY_CONFIG_DIR"])
        assert sandbox_config.is_dir()
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout)

    return run


def test_sandboxed_self_test_passes_and_runs_in_a_throwaway_directory(monkeypatch):
    calls: list[RecordedRun] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(0, "OK\n", calls))

    result = doctor_cmd.run_sandboxed_self_test()

    assert isinstance(result, doctor_cmd.SelfTestPassed)
    ((argv, environment),) = calls
    assert argv[-2:] == ["doctor", "--self-test"]
    # The sandbox is removed once the self-test finishes.
    assert not Path(environment["BASIC_MEMORY_CONFIG_DIR"]).exists()


def test_sandboxed_self_test_failure_keeps_the_end_of_its_output(monkeypatch):
    output = "\n".join(f"line {number}" for number in range(30))
    monkeypatch.setattr(subprocess, "run", _fake_run(1, output, []))

    result = doctor_cmd.run_sandboxed_self_test()

    assert isinstance(result, doctor_cmd.SelfTestFailed)
    assert result.output.splitlines() == [f"line {number}" for number in range(10, 30)]


def test_sandboxed_self_test_timeout_is_a_failure(monkeypatch):
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", run)

    result = doctor_cmd.run_sandboxed_self_test()

    assert result == doctor_cmd.SelfTestFailed(
        output=f"timed out after {doctor_cmd.SELF_TEST_TIMEOUT_SECONDS}s"
    )


# --- Self-test materialization wait ---


@pytest.mark.asyncio
async def test_doctor_waits_for_deferred_api_note_materialization(tmp_path, monkeypatch):
    api_file = tmp_path / "doctor" / "Doctor API Note.md"

    async def materialize_note() -> None:
        api_file.parent.mkdir(parents=True)
        api_file.write_text("# Doctor API Note", encoding="utf-8")

    monkeypatch.setattr(
        note_content_materialization,
        "drain_pending_materializations",
        materialize_note,
    )

    content = await doctor_cmd._read_materialized_api_note(
        api_file,
        "doctor/Doctor API Note.md",
    )

    assert content == "# Doctor API Note"


@pytest.mark.asyncio
async def test_doctor_reports_api_note_missing_after_materialization_drain(tmp_path, monkeypatch):
    async def drain_without_writing() -> None:
        pass

    monkeypatch.setattr(
        note_content_materialization,
        "drain_pending_materializations",
        drain_without_writing,
    )

    with pytest.raises(ValueError, match="API note file missing: doctor/missing.md"):
        await doctor_cmd._read_materialized_api_note(
            tmp_path / "doctor" / "missing.md",
            "doctor/missing.md",
        )
