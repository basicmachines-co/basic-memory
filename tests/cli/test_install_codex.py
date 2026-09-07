"""Codex installation delegates to its CLI without initializing Basic Memory."""

import subprocess
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from basic_memory.cli.commands import install
from basic_memory.cli.main import app

runner = CliRunner()


@pytest.fixture
def codex_run(monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.setattr(install.shutil, "which", lambda name: "/bin/codex")
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(install.subprocess, "run", run)
    monkeypatch.setattr("basic_memory.cli.app.init_cli_logging", Mock(side_effect=AssertionError))
    return run


def test_dry_run_without_codex(codex_run: Mock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install.shutil, "which", lambda name: None)
    result = runner.invoke(app, ["install", "codex", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "codex plugin marketplace add basicmachines-co/basic-memory" in result.output
    assert "codex plugin add codex@basic-memory" in result.output
    codex_run.assert_not_called()


@pytest.mark.parametrize("options,answer", [(["--yes"], None), ([], "y\n")])
def test_install(codex_run: Mock, options: list[str], answer: str | None) -> None:
    result = runner.invoke(app, ["install", "codex", *options], input=answer)
    assert result.exit_code == 0, result.output
    assert [call.args[0] for call in codex_run.call_args_list] == [
        ["/bin/codex", "plugin", "marketplace", "add", "basicmachines-co/basic-memory"],
        ["/bin/codex", "plugin", "add", "codex@basic-memory"],
    ]
    assert "$bm-setup" in result.output


def test_local_source_is_one_argument(codex_run: Mock) -> None:
    source = "/local checkout/basic-memory"
    result = runner.invoke(app, ["install", "codex", "--source", source, "-y"])
    assert result.exit_code == 0, result.output
    assert codex_run.call_args_list[0].args[0][-1] == source


def test_decline(codex_run: Mock) -> None:
    result = runner.invoke(app, ["install", "codex"], input="n\n")
    assert result.exit_code != 0
    codex_run.assert_not_called()


def test_missing_codex(codex_run: Mock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install.shutil, "which", lambda name: None)
    result = runner.invoke(app, ["install", "codex", "-y"])
    assert result.exit_code == 1
    assert "Codex CLI not found" in result.output
    codex_run.assert_not_called()


@pytest.mark.parametrize("failed_step", [0, 1])
def test_failure_stops_install(codex_run: Mock, failed_step: int) -> None:
    codex_run.side_effect = [subprocess.CompletedProcess([], 0)] * failed_step + [
        subprocess.CompletedProcess([], 2)
    ]
    result = runner.invoke(app, ["install", "codex", "-y"])
    assert result.exit_code == 1
    assert codex_run.call_count == failed_step + 1
    assert "Codex command failed" in result.output
    assert "plugin installed" not in result.output


def test_launch_failure(codex_run: Mock) -> None:
    codex_run.side_effect = OSError("launch failed")
    result = runner.invoke(app, ["install", "codex", "-y"])
    assert result.exit_code == 1
    assert "Cannot launch Codex CLI" in result.output
    assert codex_run.call_count == 1
