"""Tests for bm project skills, the per-project trust switch for skill notes."""

import json
import os
import threading

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app

# Importing the commands module registers the project subcommands with the app
import basic_memory.cli.commands.project  # noqa: F401


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None

    config_dir = tmp_path / ".basic-memory"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "config.json"
    path.write_text(
        json.dumps(
            {"env": "dev", "projects": {"main": {"path": str(tmp_path / "main")}}},
            indent=2,
        )
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    return path


def _serve_skills(config_file) -> bool:
    return json.loads(config_file.read_text())["projects"]["main"]["serve_skills"]


def test_project_skills_turns_serving_on_and_off(config_file) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["project", "skills", "main"])
    assert result.exit_code == 0, result.stdout
    assert "Serving skill notes from 'main'" in result.stdout
    assert _serve_skills(config_file) is True

    result = runner.invoke(app, ["project", "skills", "main", "--off"])
    assert result.exit_code == 0, result.stdout
    assert "Stopped serving skill notes from 'main'" in result.stdout
    assert _serve_skills(config_file) is False


def test_project_skills_refuses_an_unknown_project(config_file) -> None:
    result = CliRunner().invoke(app, ["project", "skills", "nope"])

    assert result.exit_code == 1
    assert "not found" in result.stdout.lower()


def test_project_skills_fails_when_the_config_write_fails(config_file) -> None:
    CliRunner().invoke(app, ["project", "skills", "main"])
    # The save writes a pid- and thread-named temp file beside config.json and
    # CliRunner runs in this thread, so a directory at that path fails the write.
    temp_path = config_file.parent / (
        f"{config_file.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    temp_path.mkdir()

    result = CliRunner().invoke(app, ["project", "skills", "main", "--off"])

    assert result.exit_code == 1
    assert "could not save config" in result.stdout
    assert "Stopped serving" not in result.stdout
    assert _serve_skills(config_file) is True
