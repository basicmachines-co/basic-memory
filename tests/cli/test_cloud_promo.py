"""Tests for CLI cloud promo messaging."""

from io import StringIO

from rich.console import Console
from typer.testing import CliRunner

from basic_memory.cli.app import app
import basic_memory
from basic_memory.cli.promo import (
    _is_interactive_session,
    maybe_show_cloud_promo,
    maybe_show_init_line,
)
from basic_memory.config import ConfigManager


def _capture_console() -> tuple[Console, StringIO]:
    """Create a Console that writes to an in-memory buffer."""
    buf = StringIO()
    return Console(file=buf, force_terminal=True), buf


# --- maybe_show_init_line tests ---


def test_init_line_shown_on_first_run():
    console, buf = _capture_console()

    maybe_show_init_line(
        "status",
        config_manager=ConfigManager(),
        is_interactive=True,
        console=console,
    )

    output = buf.getvalue()
    assert "Basic Memory initialized" in output
    assert "✓" in output


def test_init_line_not_shown_when_already_shown():
    config_manager = ConfigManager()
    config = config_manager.load_config()
    config.cloud_promo_first_run_shown = True
    config_manager.save_config(config)

    console, buf = _capture_console()
    maybe_show_init_line(
        "status",
        config_manager=config_manager,
        is_interactive=True,
        console=console,
    )

    assert buf.getvalue() == ""


def test_init_line_not_shown_for_mcp():
    console, buf = _capture_console()
    maybe_show_init_line(
        "mcp",
        config_manager=ConfigManager(),
        is_interactive=True,
        console=console,
    )

    assert buf.getvalue() == ""


def test_init_line_not_shown_when_env_disables_promos(monkeypatch):
    monkeypatch.setenv("BASIC_MEMORY_NO_PROMOS", "1")

    console, buf = _capture_console()
    maybe_show_init_line(
        "status",
        config_manager=ConfigManager(),
        is_interactive=True,
        console=console,
    )

    assert buf.getvalue() == ""


def test_init_line_not_shown_when_not_interactive():
    console, buf = _capture_console()
    maybe_show_init_line(
        "status",
        config_manager=ConfigManager(),
        is_interactive=False,
        console=console,
    )

    assert buf.getvalue() == ""


# --- maybe_show_cloud_promo tests ---


def test_first_run_shows_cloud_panel_and_persists_flags():
    console, buf = _capture_console()

    maybe_show_cloud_promo(
        "status",
        config_manager=ConfigManager(),
        is_interactive=True,
        console=console,
    )

    output = buf.getvalue()
    # Benefit-led copy
    assert "Your knowledge, everywhere" in output
    assert "Stop losing context" in output
    assert "Basic Memory Cloud syncs your memory" in output
    assert "BMFOSS" in output
    assert "bm cloud login" in output
    # Rich Panel title
    assert "Basic Memory Cloud" in output
    # Footer hints below the panel
    assert "basicmemory.com" in output
    assert "bm cloud promo --off" in output

    config = ConfigManager().load_config()
    assert config.cloud_promo_first_run_shown is True
    assert config.cloud_promo_last_version_shown == basic_memory.__version__


def test_version_notice_shows_when_promo_version_changes():
    config_manager = ConfigManager()
    config = config_manager.load_config()
    config.cloud_promo_first_run_shown = True
    config.cloud_promo_last_version_shown = "2025-01-01"
    config_manager.save_config(config)

    console, buf = _capture_console()
    maybe_show_cloud_promo(
        "status",
        config_manager=config_manager,
        is_interactive=True,
        console=console,
    )

    output = buf.getvalue()
    # Same benefit-led copy for both first-run and version-bump
    assert "Your knowledge, everywhere" in output


def test_no_message_when_already_shown_for_current_version():
    config_manager = ConfigManager()
    config = config_manager.load_config()
    config.cloud_promo_first_run_shown = True
    config.cloud_promo_last_version_shown = basic_memory.__version__
    config_manager.save_config(config)

    console, buf = _capture_console()
    maybe_show_cloud_promo(
        "status",
        config_manager=config_manager,
        is_interactive=True,
        console=console,
    )

    assert buf.getvalue() == ""


def test_no_message_when_cloud_access_is_configured():
    config_manager = ConfigManager()
    config = config_manager.load_config()
    config.cloud_api_key = "bmc_test_key_123"
    config_manager.save_config(config)

    console, buf = _capture_console()
    maybe_show_cloud_promo(
        "status",
        config_manager=config_manager,
        is_interactive=True,
        console=console,
    )

    assert buf.getvalue() == ""


def test_no_message_when_user_opted_out():
    config_manager = ConfigManager()
    config = config_manager.load_config()
    config.cloud_promo_opt_out = True
    config_manager.save_config(config)

    console, buf = _capture_console()
    maybe_show_cloud_promo(
        "status",
        config_manager=config_manager,
        is_interactive=True,
        console=console,
    )

    assert buf.getvalue() == ""


def test_no_message_for_mcp_subcommand():
    console, buf = _capture_console()
    maybe_show_cloud_promo(
        "mcp",
        config_manager=ConfigManager(),
        is_interactive=True,
        console=console,
    )

    assert buf.getvalue() == ""


def test_no_message_when_env_disables_promos(monkeypatch):
    monkeypatch.setenv("BASIC_MEMORY_NO_PROMOS", "1")

    console, buf = _capture_console()
    maybe_show_cloud_promo(
        "status",
        config_manager=ConfigManager(),
        is_interactive=True,
        console=console,
    )

    assert buf.getvalue() == ""


def test_no_message_when_not_interactive():
    console, buf = _capture_console()
    maybe_show_cloud_promo(
        "status",
        config_manager=ConfigManager(),
        is_interactive=False,
        console=console,
    )

    assert buf.getvalue() == ""


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


# --- _is_interactive_session tests ---


def test_is_interactive_session_returns_false_when_streams_closed(monkeypatch):
    """isatty() raises ValueError on closed file descriptors (e.g., MCP shutdown)."""

    class ClosedStream:
        def isatty(self):
            raise ValueError("I/O operation on closed file")

    monkeypatch.setattr("sys.stdin", ClosedStream())
    assert _is_interactive_session() is False
