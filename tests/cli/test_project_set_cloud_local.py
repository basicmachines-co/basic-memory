"""Tests for bm project set-cloud and bm project set-local commands."""

import json

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app

# Importing the commands module registers the project subcommands with the app
import basic_memory.cli.commands.project  # noqa: F401


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_config(tmp_path, monkeypatch):
    """Create a mock config with projects for testing set-cloud/set-local."""
    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None

    config_dir = tmp_path / ".basic-memory"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.json"

    config_data = {
        "env": "dev",
        "projects": {
            "main": str(tmp_path / "main"),
            "research": str(tmp_path / "research"),
        },
        "default_project": "main",
        "cloud_api_key": "bmc_test_key_123",
        "project_modes": {},
    }

    config_file.write_text(json.dumps(config_data, indent=2))

    monkeypatch.setenv("HOME", str(tmp_path))

    yield config_file


class TestSetCloud:
    """Tests for bm project set-cloud command."""

    def test_set_cloud_success(self, runner, mock_config):
        """Test setting a project to cloud mode."""
        result = runner.invoke(app, ["project", "set-cloud", "research"])
        assert result.exit_code == 0
        assert "cloud mode" in result.stdout.lower()

        # Verify config was updated
        config_data = json.loads(mock_config.read_text())
        assert config_data["project_modes"]["research"] == "cloud"

    def test_set_cloud_nonexistent_project(self, runner, mock_config):
        """Test set-cloud with a project that doesn't exist in config."""
        result = runner.invoke(app, ["project", "set-cloud", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_set_cloud_no_credentials(self, runner, tmp_path, monkeypatch):
        """Test set-cloud when neither API key nor OAuth session is available."""
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None

        config_dir = tmp_path / ".basic-memory"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.json"

        # Config without cloud_api_key
        config_data = {
            "env": "dev",
            "projects": {"research": str(tmp_path / "research")},
            "default_project": "research",
        }
        config_file.write_text(json.dumps(config_data, indent=2))
        monkeypatch.setenv("HOME", str(tmp_path))

        result = runner.invoke(app, ["project", "set-cloud", "research"])
        assert result.exit_code == 1
        assert "no cloud credentials" in result.stdout.lower()

    def test_set_cloud_with_oauth_session(self, runner, tmp_path, monkeypatch):
        """Test set-cloud succeeds with OAuth token but no API key."""
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None

        config_dir = tmp_path / ".basic-memory"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.json"

        # Config without cloud_api_key but with a project
        config_data = {
            "env": "dev",
            "projects": {"research": str(tmp_path / "research")},
            "default_project": "research",
        }
        config_file.write_text(json.dumps(config_data, indent=2))
        monkeypatch.setenv("HOME", str(tmp_path))

        # Write OAuth token file so CLIAuth.load_tokens() returns something
        token_file = config_dir / "basic-memory-cloud.json"
        token_data = {
            "access_token": "oauth-token-789",
            "refresh_token": None,
            "expires_at": 9999999999,
            "token_type": "Bearer",
        }
        token_file.write_text(json.dumps(token_data, indent=2))

        result = runner.invoke(app, ["project", "set-cloud", "research"])
        assert result.exit_code == 0
        assert "cloud mode" in result.stdout.lower()

        # Verify config was updated
        config_data = json.loads(config_file.read_text())
        assert config_data["project_modes"]["research"] == "cloud"


class TestSetLocal:
    """Tests for bm project set-local command."""

    def test_set_local_success(self, runner, mock_config):
        """Test reverting a project to local mode."""
        # First set to cloud
        runner.invoke(app, ["project", "set-cloud", "research"])
        config_data = json.loads(mock_config.read_text())
        assert config_data["project_modes"]["research"] == "cloud"

        # Now set back to local
        result = runner.invoke(app, ["project", "set-local", "research"])
        assert result.exit_code == 0
        assert "local mode" in result.stdout.lower()

        # Verify config was updated — LOCAL removes the entry
        config_data = json.loads(mock_config.read_text())
        assert "research" not in config_data.get("project_modes", {})

    def test_set_local_nonexistent_project(self, runner, mock_config):
        """Test set-local with a project that doesn't exist in config."""
        result = runner.invoke(app, ["project", "set-local", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_set_local_already_local(self, runner, mock_config):
        """Test set-local on a project that's already local (no-op, should succeed)."""
        result = runner.invoke(app, ["project", "set-local", "main"])
        assert result.exit_code == 0
        assert "local mode" in result.stdout.lower()
