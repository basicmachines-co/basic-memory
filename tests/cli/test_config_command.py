"""Tests for the `bm config` command group (issue #991)."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.config import BasicMemoryConfig

# Importing registers the config subcommands on the shared app instance.
import basic_memory.cli.commands.config as config_cmd  # noqa: F401


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def write_config(tmp_path, monkeypatch):
    """Write config.json under a temporary HOME and return the file path."""

    def _write(config_data: dict) -> Path:
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None

        config_dir = tmp_path / ".basic-memory"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps(config_data, indent=2))
        monkeypatch.setenv("HOME", str(tmp_path))
        return config_file

    return _write


def _base_config(**overrides) -> dict:
    data = {
        "env": "dev",
        "projects": {"main": {"path": "/tmp/main", "mode": "local"}},
        "default_project": "main",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# CONFIGURABLE_FIELDS derivation
# ---------------------------------------------------------------------------


def test_configurable_fields_excludes_structured_types():
    """Structured fields need dedicated commands or richer parsing, so they're excluded."""
    assert "projects" not in config_cmd.CONFIGURABLE_FIELDS
    assert "formatters" not in config_cmd.CONFIGURABLE_FIELDS
    assert "auto_update_last_checked_at" not in config_cmd.CONFIGURABLE_FIELDS


def test_configurable_fields_includes_scalar_settings():
    """Scalar settings (str/bool/int/float/Literal/Enum) are derived from the model."""
    for expected in ("cli_output_style", "log_level", "kebab_filenames", "database_backend"):
        assert expected in config_cmd.CONFIGURABLE_FIELDS


def test_configurable_fields_matches_model_field_count():
    """Every configurable field name must be a real BasicMemoryConfig field."""
    assert set(config_cmd.CONFIGURABLE_FIELDS) <= set(BasicMemoryConfig.model_fields)


# ---------------------------------------------------------------------------
# get / list default behavior
# ---------------------------------------------------------------------------


def test_config_get_default_value(runner, write_config):
    write_config(_base_config())

    result = runner.invoke(app, ["config", "get", "cli_output_style"])

    assert result.exit_code == 0, result.output
    assert "cli_output_style = rich" in result.output


def test_config_get_unknown_key(runner, write_config):
    write_config(_base_config())

    result = runner.invoke(app, ["config", "get", "not_a_real_setting"])

    assert result.exit_code == 1
    assert "not a recognized setting" in result.output


def test_config_get_renders_enum_value_not_repr(runner, write_config):
    """Enum-typed settings (e.g. database_backend) must show their value, not `Class.MEMBER`."""
    write_config(_base_config())

    result = runner.invoke(app, ["config", "get", "database_backend"])

    assert result.exit_code == 0, result.output
    assert "database_backend = sqlite" in result.output
    assert "DatabaseBackend" not in result.output


def test_config_list_shows_default_source(runner, write_config):
    write_config(_base_config())

    result = runner.invoke(app, ["config", "list"])

    assert result.exit_code == 0, result.output
    assert "cli_output_style" in result.output
    assert "default" in result.output


def test_config_list_shows_file_source_for_set_field(runner, write_config):
    write_config(_base_config(log_level="DEBUG"))

    result = runner.invoke(app, ["config", "list", "--json"])

    assert result.exit_code == 0, result.output
    rows = {row["key"]: row for row in json.loads(result.output)}
    assert rows["log_level"]["value"] == "DEBUG"
    assert rows["log_level"]["source"] == "file"


def test_config_list_json_output_is_valid_json(runner, write_config):
    write_config(_base_config())

    result = runner.invoke(app, ["config", "list", "--json"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert any(row["key"] == "cli_output_style" for row in rows)


# ---------------------------------------------------------------------------
# set: validation through BasicMemoryConfig
# ---------------------------------------------------------------------------


def test_config_set_valid_value_round_trips(runner, write_config):
    config_file = write_config(_base_config())

    result = runner.invoke(app, ["config", "set", "cli_output_style", "plain"])
    assert result.exit_code == 0, result.output
    assert "cli_output_style = plain" in result.output

    on_disk = json.loads(config_file.read_text())
    assert on_disk["cli_output_style"] == "plain"

    get_result = runner.invoke(app, ["config", "get", "cli_output_style"])
    assert "cli_output_style = plain" in get_result.output


def test_config_set_invalid_value_fails_with_pydantic_error(runner, write_config):
    config_file = write_config(_base_config())

    result = runner.invoke(app, ["config", "set", "cli_output_style", "bogus"])

    assert result.exit_code == 1
    assert "invalid value" in result.output.lower()
    assert "cli_output_style" in result.output

    # Config file must be untouched by a failed validation.
    on_disk = json.loads(config_file.read_text())
    assert "cli_output_style" not in on_disk


def test_config_set_coerces_bool_from_string(runner, write_config):
    write_config(_base_config())

    result = runner.invoke(app, ["config", "set", "format_on_save", "true"])

    assert result.exit_code == 0, result.output
    assert "format_on_save = True" in result.output


def test_config_set_rejects_structured_field(runner, write_config):
    write_config(_base_config())

    result = runner.invoke(app, ["config", "set", "projects", '{"x": "/tmp/x"}'])

    assert result.exit_code == 1
    assert "not a recognized setting" in result.output


def test_config_set_unknown_key(runner, write_config):
    write_config(_base_config())

    result = runner.invoke(app, ["config", "set", "not_a_real_setting", "value"])

    assert result.exit_code == 1
    assert "not a recognized setting" in result.output


# ---------------------------------------------------------------------------
# unset: revert to default
# ---------------------------------------------------------------------------


def test_config_unset_reverts_to_default(runner, write_config):
    write_config(_base_config(cli_output_style="plain"))

    result = runner.invoke(app, ["config", "unset", "cli_output_style"])

    assert result.exit_code == 0, result.output
    assert "reverted to default: rich" in result.output

    get_result = runner.invoke(app, ["config", "get", "cli_output_style"])
    assert "cli_output_style = rich" in get_result.output


def test_config_unset_unknown_key(runner, write_config):
    write_config(_base_config())

    result = runner.invoke(app, ["config", "unset", "not_a_real_setting"])

    assert result.exit_code == 1
    assert "not a recognized setting" in result.output


# ---------------------------------------------------------------------------
# Redaction: cloud_api_key must never print, database_url credentials masked
# ---------------------------------------------------------------------------


def test_config_get_never_prints_cloud_api_key(runner, write_config):
    write_config(_base_config(cloud_api_key="bmc_super_secret_token"))

    result = runner.invoke(app, ["config", "get", "cloud_api_key"])

    assert result.exit_code == 0, result.output
    assert "bmc_super_secret_token" not in result.output
    assert "cloud_api_key = ********" in result.output


def test_config_list_never_prints_cloud_api_key(runner, write_config):
    write_config(_base_config(cloud_api_key="bmc_super_secret_token"))

    result = runner.invoke(app, ["config", "list", "--json"])

    assert result.exit_code == 0, result.output
    assert "bmc_super_secret_token" not in result.output
    rows = {row["key"]: row for row in json.loads(result.output)}
    assert rows["cloud_api_key"]["value"] == "********"


def test_config_get_masks_database_url_credentials(runner, write_config):
    write_config(_base_config(database_url="postgresql://dbuser:dbpass@host.example.com:5432/bm"))

    result = runner.invoke(app, ["config", "get", "database_url"])

    assert result.exit_code == 0, result.output
    assert "dbpass" not in result.output
    assert "dbuser" not in result.output
    assert "host.example.com" in result.output


def test_config_get_shows_not_set_for_unset_secret(runner, write_config):
    write_config(_base_config())

    result = runner.invoke(app, ["config", "get", "cloud_api_key"])

    assert result.exit_code == 0, result.output
    assert "cloud_api_key = (not set)" in result.output


# ---------------------------------------------------------------------------
# Env var overrides
# ---------------------------------------------------------------------------


def test_config_get_shows_env_override(runner, write_config, monkeypatch):
    write_config(_base_config())
    monkeypatch.setenv("BASIC_MEMORY_CLI_OUTPUT_STYLE", "plain")

    result = runner.invoke(app, ["config", "get", "cli_output_style"])

    assert result.exit_code == 0, result.output
    assert "cli_output_style = plain" in result.output
    assert "BASIC_MEMORY_CLI_OUTPUT_STYLE" in result.output


def test_config_get_masks_secret_env_override(runner, write_config, monkeypatch):
    write_config(_base_config())
    monkeypatch.setenv("BASIC_MEMORY_CLOUD_API_KEY", "bmc_env_secret_token")

    result = runner.invoke(app, ["config", "get", "cloud_api_key"])

    assert result.exit_code == 0, result.output
    assert "bmc_env_secret_token" not in result.output
    assert "********" in result.output


def test_config_list_shows_env_source(runner, write_config, monkeypatch):
    write_config(_base_config())
    monkeypatch.setenv("BASIC_MEMORY_CLI_OUTPUT_STYLE", "plain")

    result = runner.invoke(app, ["config", "list", "--json"])

    assert result.exit_code == 0, result.output
    rows = {row["key"]: row for row in json.loads(result.output)}
    assert rows["cli_output_style"]["value"] == "plain"
    assert "env" in rows["cli_output_style"]["source"]
    assert "BASIC_MEMORY_CLI_OUTPUT_STYLE" in rows["cli_output_style"]["source"]


def test_config_set_warns_when_env_var_overrides(runner, write_config, monkeypatch):
    write_config(_base_config())
    monkeypatch.setenv("BASIC_MEMORY_CLI_OUTPUT_STYLE", "plain")

    result = runner.invoke(app, ["config", "set", "cli_output_style", "rich"])

    assert result.exit_code == 0, result.output
    assert "BASIC_MEMORY_CLI_OUTPUT_STYLE is set" in result.output
