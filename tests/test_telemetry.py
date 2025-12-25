"""Unit tests for telemetry module."""

import os
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.telemetry import (
    get_client,
    get_global_properties,
    get_install_id,
    is_telemetry_enabled,
    show_telemetry_notice,
    track,
)


@pytest.fixture
def mock_config_manager(tmp_path, monkeypatch):
    """Mock ConfigManager for testing."""
    config_dir = tmp_path / ".basic-memory"
    config_dir.mkdir(parents=True, exist_ok=True)

    # Mock the config directory
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(config_dir))

    # Clear the module-level cache
    import basic_memory.config

    basic_memory.config._CONFIG_CACHE = None

    # Clear telemetry module state
    import basic_memory.telemetry

    basic_memory.telemetry._client = None
    basic_memory.telemetry._telemetry_checked = False

    yield ConfigManager()

    # Clean up
    basic_memory.config._CONFIG_CACHE = None
    basic_memory.telemetry._client = None
    basic_memory.telemetry._telemetry_checked = False


@pytest.fixture
def install_id_file(tmp_path, monkeypatch):
    """Create a temporary install ID file."""
    install_dir = tmp_path / ".basic-memory"
    install_dir.mkdir(parents=True, exist_ok=True)
    install_id_path = install_dir / ".install_id"

    # Mock Path.home() to return tmp_path
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    return install_id_path


def test_get_install_id_creates_new_id(install_id_file):
    """Test that get_install_id creates a new ID if none exists."""
    # Ensure file doesn't exist
    if install_id_file.exists():
        install_id_file.unlink()

    install_id = get_install_id()

    # Verify ID is a valid UUID
    uuid.UUID(install_id)

    # Verify file was created
    assert install_id_file.exists()
    assert install_id_file.read_text().strip() == install_id


def test_get_install_id_returns_existing_id(install_id_file):
    """Test that get_install_id returns existing ID if it exists."""
    # Create an existing ID
    existing_id = str(uuid.uuid4())
    install_id_file.write_text(existing_id)

    install_id = get_install_id()

    assert install_id == existing_id


def test_is_telemetry_enabled_env_false(mock_config_manager, monkeypatch):
    """Test that BASIC_MEMORY_TELEMETRY_ENABLED=false disables telemetry."""
    monkeypatch.setenv("BASIC_MEMORY_TELEMETRY_ENABLED", "false")

    assert is_telemetry_enabled() is False


def test_is_telemetry_enabled_env_true(mock_config_manager, monkeypatch):
    """Test that BASIC_MEMORY_TELEMETRY_ENABLED=true enables telemetry."""
    monkeypatch.setenv("BASIC_MEMORY_TELEMETRY_ENABLED", "true")

    assert is_telemetry_enabled() is True


def test_is_telemetry_enabled_config_false(mock_config_manager, monkeypatch):
    """Test that config file value is used if env var not set."""
    # Ensure env var is not set
    monkeypatch.delenv("BASIC_MEMORY_TELEMETRY_ENABLED", raising=False)

    # Set config value
    config = mock_config_manager.load_config()
    config.telemetry_enabled = False
    mock_config_manager.save_config(config)

    # Clear cache
    import basic_memory.config

    basic_memory.config._CONFIG_CACHE = None

    assert is_telemetry_enabled() is False


def test_is_telemetry_enabled_config_true(mock_config_manager, monkeypatch):
    """Test that config file value is used if env var not set."""
    # Ensure env var is not set
    monkeypatch.delenv("BASIC_MEMORY_TELEMETRY_ENABLED", raising=False)

    # Set config value
    config = mock_config_manager.load_config()
    config.telemetry_enabled = True
    mock_config_manager.save_config(config)

    # Clear cache
    import basic_memory.config

    basic_memory.config._CONFIG_CACHE = None

    assert is_telemetry_enabled() is True


def test_is_telemetry_enabled_default_true(mock_config_manager, monkeypatch):
    """Test that telemetry is enabled by default."""
    # Ensure env var is not set
    monkeypatch.delenv("BASIC_MEMORY_TELEMETRY_ENABLED", raising=False)

    # Don't set config value - use default
    assert is_telemetry_enabled() is True


def test_get_client_disabled(mock_config_manager, monkeypatch):
    """Test that get_client returns None when telemetry is disabled."""
    monkeypatch.setenv("BASIC_MEMORY_TELEMETRY_ENABLED", "false")

    client = get_client()
    assert client is None


@patch("basic_memory.telemetry.OpenPanel")
def test_get_client_import_error(mock_openpanel, mock_config_manager, monkeypatch):
    """Test that get_client handles OpenPanel import errors gracefully."""
    monkeypatch.setenv("BASIC_MEMORY_TELEMETRY_ENABLED", "true")

    # Simulate ImportError
    with patch("basic_memory.telemetry.OpenPanel", side_effect=ImportError):
        # Reset telemetry module state
        import basic_memory.telemetry

        basic_memory.telemetry._client = None
        basic_memory.telemetry._telemetry_checked = False

        client = get_client()
        assert client is None


def test_get_global_properties(install_id_file):
    """Test that get_global_properties returns expected properties."""
    properties = get_global_properties()

    assert "app_version" in properties
    assert "python_version" in properties
    assert "os" in properties
    assert "arch" in properties
    assert "install_id" in properties

    # Verify install_id is a valid UUID
    uuid.UUID(properties["install_id"])


def test_track_disabled(mock_config_manager, monkeypatch):
    """Test that track does nothing when telemetry is disabled."""
    monkeypatch.setenv("BASIC_MEMORY_TELEMETRY_ENABLED", "false")

    # Reset telemetry module state
    import basic_memory.telemetry

    basic_memory.telemetry._client = None
    basic_memory.telemetry._telemetry_checked = False

    # This should not raise any exceptions
    track("test_event", {"key": "value"})


@patch("basic_memory.telemetry.OpenPanel")
def test_track_enabled(mock_openpanel_class, mock_config_manager, monkeypatch, install_id_file):
    """Test that track sends events when telemetry is enabled."""
    monkeypatch.setenv("BASIC_MEMORY_TELEMETRY_ENABLED", "true")
    monkeypatch.setenv("OPENPANEL_CLIENT_ID", "test-client")
    monkeypatch.setenv("OPENPANEL_CLIENT_SECRET", "test-secret")

    # Reset telemetry module state
    import basic_memory.telemetry

    basic_memory.telemetry._client = None
    basic_memory.telemetry._telemetry_checked = False

    # Create mock client instance
    mock_client = MagicMock()
    mock_openpanel_class.return_value = mock_client

    # Track an event
    track("test_event", {"key": "value"})

    # Verify OpenPanel was initialized
    mock_openpanel_class.assert_called_once_with(
        client_id="test-client", client_secret="test-secret"
    )

    # Verify track was called with correct arguments
    mock_client.track.assert_called_once()
    call_args = mock_client.track.call_args[0]
    assert call_args[0] == "test_event"
    assert "key" in call_args[1]
    assert call_args[1]["key"] == "value"
    assert "app_version" in call_args[1]
    assert "install_id" in call_args[1]


def test_track_exception_handling(mock_config_manager, monkeypatch):
    """Test that track handles exceptions gracefully."""
    monkeypatch.setenv("BASIC_MEMORY_TELEMETRY_ENABLED", "true")

    # Reset telemetry module state
    import basic_memory.telemetry

    basic_memory.telemetry._client = None
    basic_memory.telemetry._telemetry_checked = False

    # Mock get_client to raise an exception
    with patch("basic_memory.telemetry.get_client", side_effect=Exception("Test error")):
        # This should not raise any exceptions
        track("test_event", {"key": "value"})


def test_show_telemetry_notice_disabled(mock_config_manager, monkeypatch, capsys):
    """Test that telemetry notice is not shown when disabled."""
    monkeypatch.setenv("BASIC_MEMORY_TELEMETRY_ENABLED", "false")

    show_telemetry_notice()

    captured = capsys.readouterr()
    assert captured.out == ""


def test_show_telemetry_notice_first_run(mock_config_manager, monkeypatch, capsys):
    """Test that telemetry notice is shown on first run."""
    monkeypatch.setenv("BASIC_MEMORY_TELEMETRY_ENABLED", "true")

    # Ensure notice hasn't been shown
    config = mock_config_manager.load_config()
    config.telemetry_notice_shown = False
    mock_config_manager.save_config(config)

    # Clear cache
    import basic_memory.config

    basic_memory.config._CONFIG_CACHE = None

    show_telemetry_notice()

    captured = capsys.readouterr()
    assert "Basic Memory collects anonymous usage statistics" in captured.out
    assert "bm telemetry disable" in captured.out

    # Verify notice_shown flag was set
    config = mock_config_manager.load_config()
    assert config.telemetry_notice_shown is True


def test_show_telemetry_notice_already_shown(mock_config_manager, monkeypatch, capsys):
    """Test that telemetry notice is only shown once."""
    monkeypatch.setenv("BASIC_MEMORY_TELEMETRY_ENABLED", "true")

    # Set notice as already shown
    config = mock_config_manager.load_config()
    config.telemetry_notice_shown = True
    mock_config_manager.save_config(config)

    # Clear cache
    import basic_memory.config

    basic_memory.config._CONFIG_CACHE = None

    show_telemetry_notice()

    captured = capsys.readouterr()
    assert captured.out == ""


def test_show_telemetry_notice_exception_handling(mock_config_manager, monkeypatch, capsys):
    """Test that show_telemetry_notice handles exceptions gracefully."""
    monkeypatch.setenv("BASIC_MEMORY_TELEMETRY_ENABLED", "true")

    # Mock load_config to raise an exception
    with patch.object(ConfigManager, "load_config", side_effect=Exception("Test error")):
        # This should not raise any exceptions
        show_telemetry_notice()

    captured = capsys.readouterr()
    # Notice should not be shown due to exception
    assert captured.out == ""
