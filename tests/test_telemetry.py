"""Tests for telemetry module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from basic_memory.config import BasicMemoryConfig


class TestGetInstallId:
    """Tests for get_install_id function."""

    def test_creates_install_id_on_first_call(self, tmp_path, monkeypatch):
        """Test that a new install ID is created on first call."""
        # Mock Path.home() to return tmp_path (works cross-platform)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        from basic_memory.telemetry import get_install_id

        install_id = get_install_id()

        # Should be a valid UUID format (36 chars with hyphens)
        assert len(install_id) == 36
        assert install_id.count("-") == 4

        # File should exist
        id_file = tmp_path / ".basic-memory" / ".install_id"
        assert id_file.exists()
        assert id_file.read_text().strip() == install_id

    def test_returns_existing_install_id(self, tmp_path, monkeypatch):
        """Test that existing install ID is returned on subsequent calls."""
        # Mock Path.home() to return tmp_path (works cross-platform)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Create the ID file first
        id_file = tmp_path / ".basic-memory" / ".install_id"
        id_file.parent.mkdir(parents=True, exist_ok=True)
        existing_id = "test-uuid-12345"
        id_file.write_text(existing_id)

        from basic_memory.telemetry import get_install_id

        install_id = get_install_id()

        assert install_id == existing_id


class TestTelemetryConfig:
    """Tests for telemetry configuration fields."""

    def test_telemetry_enabled_defaults_to_true(self, config_home, monkeypatch):
        """Test that telemetry is enabled by default (Homebrew model)."""
        # Clear config cache
        import basic_memory.config

        basic_memory.config._CONFIG_CACHE = None

        config = BasicMemoryConfig()
        assert config.telemetry_enabled is True

    def test_telemetry_notice_shown_defaults_to_false(self, config_home, monkeypatch):
        """Test that telemetry notice starts as not shown."""
        # Clear config cache
        import basic_memory.config

        basic_memory.config._CONFIG_CACHE = None

        config = BasicMemoryConfig()
        assert config.telemetry_notice_shown is False

    def test_telemetry_enabled_via_env_var(self, config_home, monkeypatch):
        """Test that telemetry can be disabled via environment variable."""
        import basic_memory.config

        basic_memory.config._CONFIG_CACHE = None

        monkeypatch.setenv("BASIC_MEMORY_TELEMETRY_ENABLED", "false")

        config = BasicMemoryConfig()
        assert config.telemetry_enabled is False


class TestTrack:
    """Tests for the track function."""

    def test_track_does_not_raise_on_error(self, config_home, monkeypatch):
        """Test that track never raises exceptions."""
        import basic_memory.config
        import basic_memory.telemetry

        basic_memory.config._CONFIG_CACHE = None
        basic_memory.telemetry._client = None
        basic_memory.telemetry._initialized = False

        # Mock OpenPanel to raise an exception
        with patch("basic_memory.telemetry.OpenPanel") as mock_openpanel:
            mock_client = MagicMock()
            mock_client.track.side_effect = Exception("Network error")
            mock_openpanel.return_value = mock_client

            from basic_memory.telemetry import track

            # Should not raise
            track("test_event", {"key": "value"})

    def test_track_respects_disabled_config(self, config_home, monkeypatch):
        """Test that track does nothing when telemetry is disabled."""
        import basic_memory.config
        import basic_memory.telemetry

        basic_memory.config._CONFIG_CACHE = None
        basic_memory.telemetry._client = None
        basic_memory.telemetry._initialized = False

        monkeypatch.setenv("BASIC_MEMORY_TELEMETRY_ENABLED", "false")

        with patch("basic_memory.telemetry.OpenPanel") as mock_openpanel:
            mock_client = MagicMock()
            mock_openpanel.return_value = mock_client

            from basic_memory.telemetry import track, reset_client

            reset_client()
            track("test_event")

            # OpenPanel should have been initialized with disabled=True
            mock_openpanel.assert_called_once()
            call_kwargs = mock_openpanel.call_args[1]
            assert call_kwargs["disabled"] is True


class TestShowNoticeIfNeeded:
    """Tests for show_notice_if_needed function."""

    def test_shows_notice_when_enabled_and_not_shown(self, config_home, tmp_path, monkeypatch):
        """Test that notice is shown on first run with telemetry enabled."""
        import basic_memory.config
        import basic_memory.telemetry

        # Reset state
        basic_memory.config._CONFIG_CACHE = None
        basic_memory.telemetry._client = None
        basic_memory.telemetry._initialized = False

        # Set up config directory
        config_dir = tmp_path / ".basic-memory"
        config_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(config_dir))

        # Create config with telemetry enabled but notice not shown
        from basic_memory.telemetry import show_notice_if_needed

        with patch("rich.console.Console") as mock_console_class:
            mock_console = MagicMock()
            mock_console_class.return_value = mock_console

            show_notice_if_needed()

            # Console should have been called to print the notice
            mock_console.print.assert_called_once()

    def test_does_not_show_notice_when_disabled(self, config_home, tmp_path, monkeypatch):
        """Test that notice is not shown when telemetry is disabled."""
        import basic_memory.config
        import basic_memory.telemetry

        # Reset state
        basic_memory.config._CONFIG_CACHE = None
        basic_memory.telemetry._client = None
        basic_memory.telemetry._initialized = False

        monkeypatch.setenv("BASIC_MEMORY_TELEMETRY_ENABLED", "false")

        config_dir = tmp_path / ".basic-memory"
        config_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(config_dir))

        from basic_memory.telemetry import show_notice_if_needed

        with patch("rich.console.Console") as mock_console_class:
            show_notice_if_needed()

            # Console should not have been instantiated
            mock_console_class.assert_not_called()


class TestConvenienceFunctions:
    """Tests for convenience tracking functions."""

    def test_track_app_started(self, config_home, monkeypatch):
        """Test track_app_started function."""
        import basic_memory.config
        import basic_memory.telemetry

        basic_memory.config._CONFIG_CACHE = None
        basic_memory.telemetry._client = None
        basic_memory.telemetry._initialized = False

        with patch("basic_memory.telemetry.OpenPanel") as mock_openpanel:
            mock_client = MagicMock()
            mock_openpanel.return_value = mock_client

            from basic_memory.telemetry import track_app_started

            track_app_started("cli")

            mock_client.track.assert_called_once_with("app_started", {"mode": "cli"})

    def test_track_mcp_tool(self, config_home, monkeypatch):
        """Test track_mcp_tool function."""
        import basic_memory.config
        import basic_memory.telemetry

        basic_memory.config._CONFIG_CACHE = None
        basic_memory.telemetry._client = None
        basic_memory.telemetry._initialized = False

        with patch("basic_memory.telemetry.OpenPanel") as mock_openpanel:
            mock_client = MagicMock()
            mock_openpanel.return_value = mock_client

            from basic_memory.telemetry import track_mcp_tool

            track_mcp_tool("write_note")

            mock_client.track.assert_called_once_with("mcp_tool_called", {"tool": "write_note"})

    def test_track_error_truncates_message(self, config_home, monkeypatch):
        """Test that track_error truncates long messages."""
        import basic_memory.config
        import basic_memory.telemetry

        basic_memory.config._CONFIG_CACHE = None
        basic_memory.telemetry._client = None
        basic_memory.telemetry._initialized = False

        with patch("basic_memory.telemetry.OpenPanel") as mock_openpanel:
            mock_client = MagicMock()
            mock_openpanel.return_value = mock_client

            from basic_memory.telemetry import track_error

            long_message = "x" * 500
            track_error("ValueError", long_message)

            call_args = mock_client.track.call_args
            assert call_args[0][0] == "error"
            assert len(call_args[0][1]["message"]) == 200  # Truncated to 200 chars

    def test_track_error_sanitizes_file_paths(self, config_home, monkeypatch):
        """Test that track_error sanitizes file paths from messages."""
        import basic_memory.config
        import basic_memory.telemetry

        basic_memory.config._CONFIG_CACHE = None
        basic_memory.telemetry._client = None
        basic_memory.telemetry._initialized = False

        with patch("basic_memory.telemetry.OpenPanel") as mock_openpanel:
            mock_client = MagicMock()
            mock_openpanel.return_value = mock_client

            from basic_memory.telemetry import track_error

            # Test Unix path sanitization
            track_error("FileNotFoundError", "No such file: /Users/john/notes/secret.md")
            call_args = mock_client.track.call_args
            assert "/Users/john" not in call_args[0][1]["message"]
            assert "[FILE]" in call_args[0][1]["message"]

            # Test Windows path sanitization
            mock_client.reset_mock()
            track_error("FileNotFoundError", "Cannot open C:\\Users\\john\\docs\\private.txt")
            call_args = mock_client.track.call_args
            assert "C:\\Users\\john" not in call_args[0][1]["message"]
            assert "[FILE]" in call_args[0][1]["message"]
