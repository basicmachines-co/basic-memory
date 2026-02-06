"""Tests for CLI cloud promo messaging."""

from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.cli.promo import CLOUD_PROMO_VERSION, maybe_show_cloud_promo
from basic_memory.config import ConfigManager


def test_first_run_shows_intro_message_and_persists_flags():
    messages: list[str] = []

    maybe_show_cloud_promo(
        "status",
        config_manager=ConfigManager(),
        is_interactive=True,
        echo=messages.append,
    )

    assert len(messages) == 1
    assert "Basic Memory initialized (local mode)." in messages[0]
    assert "{{OSS_DISCOUNT_CODE}}" in messages[0]

    config = ConfigManager().load_config()
    assert config.cloud_promo_first_run_shown is True
    assert config.cloud_promo_last_version_shown == CLOUD_PROMO_VERSION


def test_version_notice_shows_when_promo_version_changes():
    config_manager = ConfigManager()
    config = config_manager.load_config()
    config.cloud_promo_first_run_shown = True
    config.cloud_promo_last_version_shown = "2025-01-01"
    config_manager.save_config(config)

    messages: list[str] = []
    maybe_show_cloud_promo(
        "status",
        config_manager=config_manager,
        is_interactive=True,
        echo=messages.append,
    )

    assert len(messages) == 1
    assert messages[0].startswith("New in Basic Memory Cloud")



def test_no_message_when_already_shown_for_current_version():
    config_manager = ConfigManager()
    config = config_manager.load_config()
    config.cloud_promo_first_run_shown = True
    config.cloud_promo_last_version_shown = CLOUD_PROMO_VERSION
    config_manager.save_config(config)

    messages: list[str] = []
    maybe_show_cloud_promo(
        "status",
        config_manager=config_manager,
        is_interactive=True,
        echo=messages.append,
    )

    assert messages == []



def test_no_message_when_cloud_mode_enabled():
    config_manager = ConfigManager()
    config = config_manager.load_config()
    config.cloud_mode = True
    config_manager.save_config(config)

    messages: list[str] = []
    maybe_show_cloud_promo(
        "status",
        config_manager=config_manager,
        is_interactive=True,
        echo=messages.append,
    )

    assert messages == []



def test_no_message_when_user_opted_out():
    config_manager = ConfigManager()
    config = config_manager.load_config()
    config.cloud_promo_opt_out = True
    config_manager.save_config(config)

    messages: list[str] = []
    maybe_show_cloud_promo(
        "status",
        config_manager=config_manager,
        is_interactive=True,
        echo=messages.append,
    )

    assert messages == []



def test_no_message_for_mcp_subcommand():
    messages: list[str] = []
    maybe_show_cloud_promo(
        "mcp",
        config_manager=ConfigManager(),
        is_interactive=True,
        echo=messages.append,
    )

    assert messages == []



def test_no_message_when_env_disables_promos(monkeypatch):
    monkeypatch.setenv("BASIC_MEMORY_NO_PROMOS", "1")

    messages: list[str] = []
    maybe_show_cloud_promo(
        "status",
        config_manager=ConfigManager(),
        is_interactive=True,
        echo=messages.append,
    )

    assert messages == []


def test_no_message_when_not_interactive():
    messages: list[str] = []
    maybe_show_cloud_promo(
        "status",
        config_manager=ConfigManager(),
        is_interactive=False,
        echo=messages.append,
    )

    assert messages == []


def test_cloud_promo_command_off_sets_opt_out(monkeypatch):
    runner = CliRunner()
    instances: list[object] = []

    class _StubConfig:
        cloud_promo_opt_out = False

    class _StubConfigManager:
        def __init__(self):
            self._config = _StubConfig()
            self.saved_config = None
            instances.append(self)

        def load_config(self):
            return self._config

        def save_config(self, config):
            self.saved_config = config

    monkeypatch.setattr(
        "basic_memory.cli.commands.cloud.core_commands.ConfigManager",
        _StubConfigManager,
    )

    result = runner.invoke(app, ["cloud", "promo", "--off"])
    assert result.exit_code == 0
    assert "Cloud promo messages disabled" in result.stdout
    assert len(instances) == 1
    assert instances[0].saved_config.cloud_promo_opt_out is True


def test_cloud_promo_command_on_clears_opt_out(monkeypatch):
    runner = CliRunner()
    instances: list[object] = []

    class _StubConfig:
        cloud_promo_opt_out = True

    class _StubConfigManager:
        def __init__(self):
            self._config = _StubConfig()
            self.saved_config = None
            instances.append(self)

        def load_config(self):
            return self._config

        def save_config(self, config):
            self.saved_config = config

    monkeypatch.setattr(
        "basic_memory.cli.commands.cloud.core_commands.ConfigManager",
        _StubConfigManager,
    )

    result = runner.invoke(app, ["cloud", "promo", "--on"])
    assert result.exit_code == 0
    assert "Cloud promo messages enabled" in result.stdout
    assert len(instances) == 1
    assert instances[0].saved_config.cloud_promo_opt_out is False
