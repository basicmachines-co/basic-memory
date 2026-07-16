"""Regression tests for side effects while loading existing configuration."""

import json
import os
from pathlib import Path

import pytest

from basic_memory import config as config_module
from basic_memory.config import ConfigManager


def test_existing_config_load_does_not_create_default_project_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading a custom project config must not materialize the unused default path."""
    home = tmp_path / "home"
    config_dir = tmp_path / "config"
    project_dir = tmp_path / "custom-project"
    home.mkdir()
    config_dir.mkdir()
    project_dir.mkdir()

    monkeypatch.setenv("HOME", str(home))
    if os.name == "nt":
        monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("BASIC_MEMORY_HOME", raising=False)
    monkeypatch.setattr(config_module, "_CONFIG_CACHE", None)
    monkeypatch.setattr(config_module, "_CONFIG_MTIME", None)
    monkeypatch.setattr(config_module, "_CONFIG_SIZE", None)

    config_path = config_dir / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "projects": {
                    "custom": {
                        "path": str(project_dir),
                        "mode": "local",
                    }
                },
                "default_project": "custom",
            }
        ),
        encoding="utf-8",
    )

    loaded = ConfigManager().load_config()

    assert loaded.projects["custom"].path == str(project_dir)
    assert not (home / "basic-memory").exists()


def test_existing_config_load_keeps_environment_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing overridden file fields still delegates environment parsing to BaseSettings."""
    config_dir = tmp_path / "config"
    project_dir = tmp_path / "custom-project"
    config_dir.mkdir()
    project_dir.mkdir()

    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("BASIC_MEMORY_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("BASIC_MEMORY_FORMATTERS", '{"md":"formatter --write {file}"}')
    monkeypatch.setattr(config_module, "_CONFIG_CACHE", None)
    monkeypatch.setattr(config_module, "_CONFIG_MTIME", None)
    monkeypatch.setattr(config_module, "_CONFIG_SIZE", None)

    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "projects": {"custom": {"path": str(project_dir)}},
                "default_project": "custom",
                "log_level": "INFO",
                "formatters": {"md": "old-formatter {file}"},
            }
        ),
        encoding="utf-8",
    )

    loaded = ConfigManager().load_config()

    assert loaded.log_level == "DEBUG"
    assert loaded.formatters == {"md": "formatter --write {file}"}
