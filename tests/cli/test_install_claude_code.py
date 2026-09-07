"""Claude Code installation delegates to its CLI without initializing Basic Memory."""

import subprocess
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from basic_memory.cli.commands import install
from basic_memory.cli.main import app

runner = CliRunner()

MARKETPLACE = [
    "/bin/claude",
    "plugin",
    "marketplace",
    "add",
    "basicmachines-co/basic-memory",
    "--scope",
    "user",
    "--sparse",
    ".claude-plugin",
    "plugins/claude-code",
]
PLUGIN = [
    "/bin/claude",
    "plugin",
    "install",
    "basic-memory@basicmachines-co",
    "--scope",
    "user",
]


@pytest.fixture
def claude_run(monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.setattr(install.shutil, "which", lambda name: "/bin/claude")
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(install.subprocess, "run", run)
    monkeypatch.setattr("basic_memory.cli.app.init_cli_logging", Mock(side_effect=AssertionError))
    return run


def test_dry_run_without_claude(claude_run: Mock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install.shutil, "which", lambda name: None)
    result = runner.invoke(app, ["install", "claude-code", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert (
        "claude plugin marketplace add basicmachines-co/basic-memory --scope user" in result.output
    )
    assert "claude plugin install basic-memory@basicmachines-co --scope user" in result.output
    # A preview must not claim an install that never ran.
    assert "plugin installed" not in result.output
    claude_run.assert_not_called()


@pytest.mark.parametrize("options,answer", [(["--yes"], None), ([], "y\n")])
def test_install(claude_run: Mock, options: list[str], answer: str | None) -> None:
    result = runner.invoke(app, ["install", "claude-code", *options], input=answer)
    assert result.exit_code == 0, result.output
    assert [call.args[0] for call in claude_run.call_args_list] == [MARKETPLACE, PLUGIN]
    assert "/basic-memory:bm-setup" in result.output


@pytest.mark.parametrize("scope", ["project", "local"])
def test_scope_applies_to_both_steps(claude_run: Mock, scope: str) -> None:
    result = runner.invoke(app, ["install", "claude-code", "--scope", scope, "-y"])
    assert result.exit_code == 0, result.output
    for call in claude_run.call_args_list:
        argv = call.args[0]
        assert argv[argv.index("--scope") + 1] == scope
    assert f"({scope}-level)" in result.output


def test_unknown_scope_is_rejected(claude_run: Mock) -> None:
    result = runner.invoke(app, ["install", "claude-code", "--scope", "global", "-y"])
    assert result.exit_code != 0
    claude_run.assert_not_called()


def test_local_source_is_one_argument_without_sparse(claude_run: Mock, tmp_path) -> None:
    # Claude Code rejects --sparse for directory sources, so a checkout must not
    # carry the sparse paths the monorepo Git source needs.
    result = runner.invoke(app, ["install", "claude-code", "--source", str(tmp_path), "-y"])
    assert result.exit_code == 0, result.output
    assert claude_run.call_args_list[0].args[0] == [
        "/bin/claude",
        "plugin",
        "marketplace",
        "add",
        str(tmp_path),
        "--scope",
        "user",
    ]


def test_missing_directory_source_keeps_sparse(claude_run: Mock, tmp_path) -> None:
    source = str(tmp_path / "absent")
    result = runner.invoke(app, ["install", "claude-code", "--source", source, "-y"])
    assert result.exit_code == 0, result.output
    assert claude_run.call_args_list[0].args[0][-3:] == [
        "--sparse",
        ".claude-plugin",
        "plugins/claude-code",
    ]


def test_decline(claude_run: Mock) -> None:
    result = runner.invoke(app, ["install", "claude-code"], input="n\n")
    assert result.exit_code != 0
    claude_run.assert_not_called()


def test_missing_claude(claude_run: Mock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install.shutil, "which", lambda name: None)
    result = runner.invoke(app, ["install", "claude-code", "-y"])
    assert result.exit_code == 1
    assert "Claude Code CLI not found" in result.output
    claude_run.assert_not_called()


@pytest.mark.parametrize("failed_step", [0, 1])
def test_failure_stops_install(claude_run: Mock, failed_step: int) -> None:
    claude_run.side_effect = [subprocess.CompletedProcess([], 0)] * failed_step + [
        subprocess.CompletedProcess([], 2)
    ]
    result = runner.invoke(app, ["install", "claude-code", "-y"])
    assert result.exit_code == 1
    assert claude_run.call_count == failed_step + 1
    assert "Claude Code command failed" in result.output
    assert "rerun bm install claude-code" in result.output
    assert "plugin installed" not in result.output


def test_launch_failure(claude_run: Mock) -> None:
    claude_run.side_effect = OSError("launch failed")
    result = runner.invoke(app, ["install", "claude-code", "-y"])
    assert result.exit_code == 1
    assert "Cannot launch Claude Code CLI" in result.output
    assert claude_run.call_count == 1
