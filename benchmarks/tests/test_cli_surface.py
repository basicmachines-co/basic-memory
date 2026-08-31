from pathlib import Path

import pytest
from typer.testing import CliRunner

from basic_memory_benchmarks import cli
from basic_memory_benchmarks.agent_tasks.models import AgentTasksConfig
from basic_memory_benchmarks.cli import app


runner = CliRunner()


def test_modern_qa_replaces_legacy_judge_command() -> None:
    qa_help = runner.invoke(app, ["run", "qa", "--help"])
    legacy_judge = runner.invoke(app, ["run", "judge", "--help"])

    assert qa_help.exit_code == 0
    assert "--answerer" in qa_help.output
    assert "--judge" in qa_help.output
    assert legacy_judge.exit_code != 0
    assert "No such command 'judge'" in legacy_judge.output


def test_full_command_has_no_legacy_judge_options() -> None:
    result = runner.invoke(app, ["run", "full", "--help"])

    assert result.exit_code == 0
    assert "--judge-model" not in result.output


def test_convert_beam_command_wired() -> None:
    result = runner.invoke(app, ["convert", "beam", "--help"])

    assert result.exit_code == 0
    assert "--dataset-root" in result.output
    assert "--output-dir" in result.output
    assert "--tier" in result.output


def test_run_beam_score_command_wired() -> None:
    result = runner.invoke(app, ["run", "beam-score", "--help"])

    assert result.exit_code == 0
    assert "--run-dir" in result.output
    assert "--judge" in result.output
    assert "--source" in result.output
    assert "--max-workers" in result.output


def test_run_agent_tasks_command_wired() -> None:
    result = runner.invoke(app, ["run", "agent-tasks", "--help"])

    assert result.exit_code == 0
    assert "--surfaces" in result.output
    assert "--model" in result.output
    assert "--tasks" in result.output
    assert "--bm-local-path" in result.output
    assert "--max-turns" in result.output
    assert "--strict-surfaces" in result.output


def test_run_agent_tasks_dedupes_repeated_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `--surfaces rich,rich` used to pass the membership check and then crash
    # mid-run creating the second identical surface home.
    captured: dict[str, list[str]] = {}

    def fake_run(config: AgentTasksConfig) -> Path:
        captured["surfaces"] = config.surfaces
        return tmp_path / "run"

    monkeypatch.setattr(cli, "run_agent_tasks", fake_run)
    script = tmp_path / "script.json"
    script.write_text('{"tasks": {}}', encoding="utf-8")
    bm_checkout = tmp_path / "bm"
    bm_checkout.mkdir()

    result = runner.invoke(
        app,
        [
            "run",
            "agent-tasks",
            "--surfaces",
            "rich,rich",
            "--model",
            f"scripted:{script}",
            "--bm-local-path",
            str(bm_checkout),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["surfaces"] == ["rich"]
