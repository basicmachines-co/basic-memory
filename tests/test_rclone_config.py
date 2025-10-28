"""Test rclone configuration management."""

import configparser
import tempfile
from pathlib import Path

import pytest

from basic_memory.cli.commands.cloud.rclone_config import (
    configure_rclone_remote,
    load_rclone_config,
    save_rclone_config,
)


@pytest.fixture
def temp_rclone_config(monkeypatch):
    """Create a temporary rclone config directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_dir = Path(temp_dir) / ".config" / "rclone"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "rclone.conf"

        # Monkeypatch get_rclone_config_path to use temp directory
        monkeypatch.setattr(
            "basic_memory.cli.commands.cloud.rclone_config.get_rclone_config_path",
            lambda: config_path,
        )

        yield config_path


def test_configure_rclone_remote(temp_rclone_config):
    """Test configuring simplified rclone remote."""
    # Configure remote
    remote_name = configure_rclone_remote(
        access_key="test_access_key",
        secret_key="test_secret_key",
        endpoint="https://test.endpoint.com",
        region="test-region",
    )

    # Should return correct remote name
    assert remote_name == "basic-memory-cloud"

    # Load and verify config
    config = load_rclone_config()

    # Should have the remote section
    assert config.has_section("basic-memory-cloud")

    # Should have correct settings
    assert config.get("basic-memory-cloud", "type") == "s3"
    assert config.get("basic-memory-cloud", "provider") == "Other"
    assert config.get("basic-memory-cloud", "access_key_id") == "test_access_key"
    assert config.get("basic-memory-cloud", "secret_access_key") == "test_secret_key"
    assert config.get("basic-memory-cloud", "endpoint") == "https://test.endpoint.com"
    assert config.get("basic-memory-cloud", "region") == "test-region"


def test_configure_rclone_remote_default_values(temp_rclone_config):
    """Test configuring remote with default endpoint and region."""
    remote_name = configure_rclone_remote(access_key="test_key", secret_key="test_secret")

    assert remote_name == "basic-memory-cloud"

    config = load_rclone_config()

    # Should use default values
    assert config.get("basic-memory-cloud", "endpoint") == "https://fly.storage.tigris.dev"
    assert config.get("basic-memory-cloud", "region") == "auto"


def test_configure_rclone_remote_updates_existing(temp_rclone_config):
    """Test that configuring remote updates existing configuration."""
    # First configuration
    configure_rclone_remote(access_key="old_key", secret_key="old_secret")

    # Update configuration
    configure_rclone_remote(access_key="new_key", secret_key="new_secret")

    config = load_rclone_config()

    # Should have updated values
    assert config.get("basic-memory-cloud", "access_key_id") == "new_key"
    assert config.get("basic-memory-cloud", "secret_access_key") == "new_secret"

    # Should only have one section (not multiple)
    sections = config.sections()
    assert sections.count("basic-memory-cloud") == 1


def test_save_and_load_rclone_config(temp_rclone_config):
    """Test saving and loading rclone config."""
    # Create config
    config = configparser.ConfigParser()
    config.add_section("test-remote")
    config.set("test-remote", "type", "s3")
    config.set("test-remote", "provider", "AWS")

    # Save config
    save_rclone_config(config)

    # Load and verify
    loaded_config = load_rclone_config()
    assert loaded_config.has_section("test-remote")
    assert loaded_config.get("test-remote", "type") == "s3"
    assert loaded_config.get("test-remote", "provider") == "AWS"


def test_load_rclone_config_nonexistent(temp_rclone_config):
    """Test loading config when file doesn't exist."""
    # Delete the config file if it exists
    if temp_rclone_config.exists():
        temp_rclone_config.unlink()

    # Should return empty config without error
    config = load_rclone_config()
    assert len(config.sections()) == 0
