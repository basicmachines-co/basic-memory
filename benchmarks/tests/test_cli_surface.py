from typer.testing import CliRunner

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
