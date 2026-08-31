import json
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from basic_memory_benchmarks import cli
from basic_memory_benchmarks.agent_tasks.models import AgentTasksConfig
from basic_memory_benchmarks.cli import app
from xafs_fixture import write_xafs_root


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
    assert "--task-manifest" in result.output
    assert "--bm-local-path" in result.output
    assert "--max-turns" in result.output
    assert "--strict-surfaces" in result.output


def test_convert_xafs_command_wired() -> None:
    result = runner.invoke(app, ["convert", "xafs", "--help"])

    assert result.exit_code == 0
    assert "--dataset-root" in result.output
    assert "--output-dir" in result.output
    assert "--personas" in result.output
    assert "--corrections" in result.output


def test_sample_xafs_command_wired() -> None:
    result = runner.invoke(app, ["sample", "xafs", "--help"])

    assert result.exit_code == 0
    assert "--dataset-root" in result.output
    assert "--personas" in result.output
    assert "--n" in result.output
    assert "--seed" in result.output
    assert "--output" in result.output


def test_convert_and_sample_xafs_end_to_end(tmp_path: Path) -> None:
    root = write_xafs_root(tmp_path)
    output_dir = tmp_path / "generated"

    convert = runner.invoke(
        app,
        [
            "convert",
            "xafs",
            "--dataset-root",
            str(root),
            "--output-dir",
            str(output_dir),
            "--personas",
            "dp_001",
        ],
    )

    assert convert.exit_code == 0, convert.output
    assert (output_dir / "tasks.json").exists()
    assert (output_dir / "conversion.json").exists()
    assert (output_dir / "groups" / "xafs-dp001" / "docs").is_dir()

    audit_dir = tmp_path / "audit"
    sample = runner.invoke(
        app,
        [
            "sample",
            "xafs",
            "--dataset-root",
            str(root),
            "--n",
            "3",
            "--output",
            str(audit_dir),
        ],
    )

    assert sample.exit_code == 0, sample.output
    assert (audit_dir / "audit-sample.json").exists()
    assert (audit_dir / "sample.md").exists()


def test_convert_xafs_missing_root_is_a_parameter_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["convert", "xafs", "--dataset-root", str(tmp_path / "missing")])

    assert result.exit_code != 0
    assert "download.sh" in result.output


def _manifest_row() -> dict[str, Any]:
    return {
        "id": "xafs-dp001-q01",
        "skill": "single_hop",
        "group": "xafs-dp001",
        "source": "supermemory/xAFS dp_001 q01 @21142b2c",
        "prompt": "What was the amount?",
        "graders": [{"kind": "judge_rubric", "rubric": "Gold answer: $2,034"}],
    }


def _agent_tasks_setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    manifest = tmp_path / "gen" / "tasks.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps([_manifest_row()]), encoding="utf-8")
    script = tmp_path / "script.json"
    script.write_text('{"tasks": {}}', encoding="utf-8")
    bm_checkout = tmp_path / "bm"
    bm_checkout.mkdir()
    return manifest, script, bm_checkout


def test_task_manifest_derives_groups_corpus_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, AgentTasksConfig] = {}

    def fake_run(config: AgentTasksConfig, **kwargs: object) -> Path:
        captured["config"] = config
        return tmp_path / "run"

    monkeypatch.setattr(cli, "run_agent_tasks", fake_run)
    manifest, script, bm_checkout = _agent_tasks_setup(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            "agent-tasks",
            "--task-manifest",
            str(manifest),
            "--model",
            f"scripted:{script}",
            "--judge",
            "claude:claude-sonnet-4-6",
            "--bm-local-path",
            str(bm_checkout),
        ],
    )

    assert result.exit_code == 0, result.output
    config = captured["config"]
    assert config.task_manifest == str(manifest)
    # The default shipped-corpus dir would checksum the wrong corpus and then
    # fail on missing group subtrees; a manifest run derives the sibling
    # groups/ dir instead.
    assert config.corpus_dir == str(manifest.parent / "groups")
    assert config.judge_spec == "claude:claude-sonnet-4-6"


def test_task_manifest_respects_explicit_corpus_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, AgentTasksConfig] = {}

    def fake_run(config: AgentTasksConfig, **kwargs: object) -> Path:
        captured["config"] = config
        return tmp_path / "run"

    monkeypatch.setattr(cli, "run_agent_tasks", fake_run)
    manifest, script, bm_checkout = _agent_tasks_setup(tmp_path)
    custom_corpus = tmp_path / "custom-groups"

    result = runner.invoke(
        app,
        [
            "run",
            "agent-tasks",
            "--task-manifest",
            str(manifest),
            "--corpus-dir",
            str(custom_corpus),
            "--model",
            f"scripted:{script}",
            "--judge",
            "claude:claude-sonnet-4-6",
            "--bm-local-path",
            str(bm_checkout),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["config"].corpus_dir == str(custom_corpus)


def test_task_manifest_judge_graded_tasks_require_judge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_run(config: AgentTasksConfig) -> Path:
        raise AssertionError("run_agent_tasks must not start without a judge")

    monkeypatch.setattr(cli, "run_agent_tasks", fail_run)
    manifest, script, bm_checkout = _agent_tasks_setup(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            "agent-tasks",
            "--task-manifest",
            str(manifest),
            "--model",
            f"scripted:{script}",
            "--bm-local-path",
            str(bm_checkout),
        ],
    )

    assert result.exit_code != 0
    assert "judge_rubric" in result.output
    assert "--judge" in result.output


def test_task_manifest_unknown_task_id_is_a_parameter_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "run_agent_tasks", lambda config: tmp_path / "run")
    manifest, script, bm_checkout = _agent_tasks_setup(tmp_path)

    result = runner.invoke(
        app,
        [
            "run",
            "agent-tasks",
            "--task-manifest",
            str(manifest),
            "--tasks",
            "nope",
            "--model",
            f"scripted:{script}",
            "--judge",
            "claude:claude-sonnet-4-6",
            "--bm-local-path",
            str(bm_checkout),
        ],
    )

    assert result.exit_code != 0
    assert "Unknown task ids" in result.output


def test_run_agent_tasks_dedupes_repeated_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `--surfaces rich,rich` used to pass the membership check and then crash
    # mid-run creating the second identical surface home.
    captured: dict[str, list[str]] = {}

    def fake_run(config: AgentTasksConfig, **kwargs: object) -> Path:
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


def test_run_agent_tasks_rejects_malformed_model_header(tmp_path: Path) -> None:
    script = tmp_path / "script.json"
    script.write_text('{"tasks": {}}', encoding="utf-8")
    bm_checkout = tmp_path / "bm"
    bm_checkout.mkdir()

    result = runner.invoke(
        app,
        [
            "run",
            "agent-tasks",
            "--model",
            f"scripted:{script}",
            "--bm-local-path",
            str(bm_checkout),
            "--model-header",
            "missing-separator",
        ],
    )

    assert result.exit_code != 0
    assert "Name=value" in result.output


def test_run_agent_tasks_passes_model_headers_to_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Headers ride only in the model-factory closure — never in the config,
    # so they can never leak into run artifacts.
    captured: dict[str, object] = {}

    def fake_run(config: AgentTasksConfig, **kwargs: object) -> Path:
        captured["config"] = config
        captured["model_factory"] = kwargs.get("model_factory")
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
            "--model",
            f"scripted:{script}",
            "--bm-local-path",
            str(bm_checkout),
            "--model-header",
            "anthropic-workspace-id=wrkspc_test",
        ],
    )

    assert result.exit_code == 0, result.output
    factory = captured["model_factory"]
    assert isinstance(factory, partial)
    assert factory.keywords["extra_headers"] == {"anthropic-workspace-id": "wrkspc_test"}
    config = captured["config"]
    assert isinstance(config, AgentTasksConfig)
    assert "wrkspc_test" not in config.model_dump_json()


def test_run_agent_tasks_model_temperature_omit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Claude 5 endpoints reject the temperature parameter entirely; 'omit'
    # drops it from requests and the choice is recorded in the run config.
    captured: dict[str, object] = {}

    def fake_run(config: AgentTasksConfig, **kwargs: object) -> Path:
        captured["config"] = config
        captured["model_factory"] = kwargs.get("model_factory")
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
            "--model",
            f"scripted:{script}",
            "--bm-local-path",
            str(bm_checkout),
            "--model-temperature",
            "omit",
        ],
    )

    assert result.exit_code == 0, result.output
    factory = captured["model_factory"]
    assert isinstance(factory, partial)
    assert factory.keywords["temperature"] is None
    config = captured["config"]
    assert isinstance(config, AgentTasksConfig)
    assert config.model_temperature is None
