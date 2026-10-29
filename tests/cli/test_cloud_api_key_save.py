"""Tests for `bm cloud api-key save`."""

from typer.testing import CliRunner

from basic_memory import config as config_module
from basic_memory.cli.main import app
from basic_memory.config import ConfigManager


def _reset_config_cache() -> None:
    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None


def test_api_key_save_prompts_without_echoing_key(config_home):
    """Omitting the argument reads the key from a hidden prompt, not argv (#1902)."""
    _reset_config_cache()

    result = CliRunner().invoke(app, ["cloud", "api-key", "save"], input="bmc_prompted123\n")

    assert result.exit_code == 0, result.output
    assert "bmc_prompted123" not in result.output
    _reset_config_cache()
    assert ConfigManager().load_config().cloud_api_key == "bmc_prompted123"


def test_api_key_save_accepts_positional_key(config_home):
    _reset_config_cache()

    result = CliRunner().invoke(app, ["cloud", "api-key", "save", "bmc_positional123"])

    assert result.exit_code == 0, result.output
    _reset_config_cache()
    assert ConfigManager().load_config().cloud_api_key == "bmc_positional123"


def test_api_key_save_rejects_prompted_key_without_prefix(config_home):
    _reset_config_cache()

    result = CliRunner().invoke(app, ["cloud", "api-key", "save"], input="not-a-key\n")

    assert result.exit_code == 1
    assert "must start with 'bmc_'" in result.output
