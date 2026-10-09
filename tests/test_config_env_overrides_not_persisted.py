"""Environment overrides must never be written into config.json (#1631).

`load_config` merges `BASIC_MEMORY_*` env values over the file. Any later save
used to dump that merged model, so a one-off `BASIC_MEMORY_LOG_LEVEL=DEBUG
bm project add ...` became permanent, and a stray `BASIC_MEMORY_PROJECT_ROOT`
kept refusing slash project names after the variable was unset (#1598).
"""

import json
from pathlib import Path
from typing import Any

import pytest

from basic_memory import config as config_module
from basic_memory.config import ConfigManager


def _reset_config_cache() -> None:
    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None


def _read_file(config_manager: ConfigManager) -> dict[str, Any]:
    return json.loads(config_manager.config_file.read_text(encoding="utf-8"))


@pytest.fixture
def file_manager(config_home: Path) -> ConfigManager:
    """A ConfigManager over a hand-written config.json under the test HOME."""
    _reset_config_cache()
    manager = ConfigManager()
    manager.config_file.write_text(
        json.dumps(
            {
                "env": "test",
                "projects": {"main": {"path": str(config_home / "main"), "mode": "local"}},
                "default_project": "main",
                "log_level": "INFO",
                "semantic_search_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    return manager


def test_project_add_keeps_file_values_under_env_override(
    file_manager: ConfigManager, config_home: Path, monkeypatch
):
    monkeypatch.setenv("BASIC_MEMORY_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED", "true")
    monkeypatch.setenv("BASIC_MEMORY_CLI_OUTPUT_STYLE", "plain")
    _reset_config_cache()

    # The env values are in effect for this process.
    loaded = file_manager.load_config()
    assert loaded.log_level == "DEBUG"
    assert loaded.semantic_search_enabled is True

    file_manager.add_project("leak", str(config_home / "leak"))

    written = _read_file(file_manager)
    assert "leak" in written["projects"]
    assert written["log_level"] == "INFO"
    assert written["semantic_search_enabled"] is False
    # The file never had this key, so the env value must not appear either.
    assert "cli_output_style" not in written


def test_project_root_env_does_not_stick_after_unset(
    file_manager: ConfigManager, config_home: Path, monkeypatch
):
    """#1598: a persisted project_root kept enforcing flat names forever."""
    monkeypatch.setenv("BASIC_MEMORY_PROJECT_ROOT", str(config_home / "root"))
    _reset_config_cache()
    assert file_manager.load_config().project_root == str(config_home / "root")

    file_manager.add_project("other", str(config_home / "other"))

    assert "project_root" not in _read_file(file_manager)

    monkeypatch.delenv("BASIC_MEMORY_PROJECT_ROOT")
    _reset_config_cache()
    assert file_manager.load_config().project_root is None


def test_first_run_save_does_not_persist_env_values(config_home: Path, monkeypatch):
    monkeypatch.setenv("BASIC_MEMORY_LOG_LEVEL", "DEBUG")
    _reset_config_cache()
    manager = ConfigManager()
    assert not manager.config_file.exists()

    assert manager.load_config().log_level == "DEBUG"

    written = _read_file(manager)
    assert "log_level" not in written
    assert "projects" in written


def test_explicit_key_is_persisted_despite_env_override(file_manager: ConfigManager, monkeypatch):
    monkeypatch.setenv("BASIC_MEMORY_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED", "true")
    _reset_config_cache()

    config = file_manager.load_config()
    config.log_level = "WARNING"
    file_manager.save_config(config, persist_env_keys={"log_level"})

    written = _read_file(file_manager)
    assert written["log_level"] == "WARNING"
    assert written["semantic_search_enabled"] is False


def test_legacy_sync_env_keeps_the_file_legacy_value(config_home: Path, monkeypatch):
    """A legacy `sync_changes` file value survives a save under the legacy env var.

    The dump only carries `index_changes`, so dropping the env value without
    carrying the file's legacy value over would lose the user's setting.
    """
    _reset_config_cache()
    manager = ConfigManager()
    manager.config_file.write_text(
        json.dumps(
            {
                "env": "test",
                "projects": {"main": {"path": str(config_home / "main"), "mode": "local"}},
                "default_project": "main",
                "sync_changes": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BASIC_MEMORY_SYNC_CHANGES", "true")

    config = manager.load_config()
    assert config.index_changes is True
    manager.save_config(config)

    assert _read_file(manager)["index_changes"] is False
