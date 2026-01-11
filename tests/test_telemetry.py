"""Tests for telemetry module (minimal mocking).

We avoid standard-library mocks and instead use small stub objects + pytest monkeypatch.
"""

from __future__ import annotations

from pathlib import Path

from basic_memory.config import BasicMemoryConfig


class _StubOpenPanel:
    def __init__(self, *, client_id: str, client_secret: str, disabled: bool = False):
        self.client_id = client_id
        self.client_secret = client_secret
        self.disabled = disabled
        self.global_properties: dict | None = None
        self.events: list[tuple[str, dict]] = []
        self.raise_on_track: Exception | None = None
        self.profile_id: str | None = None

    def identify(self, profile_id: str) -> None:
        self.profile_id = profile_id

    def set_global_properties(self, props: dict) -> None:
        self.global_properties = props

    def track(self, event: str, properties: dict) -> None:
        if self.raise_on_track:
            raise self.raise_on_track
        self.events.append((event, properties))


class _StubConsole:
    def __init__(self, *args, **kwargs):
        self.print_calls: list[tuple[tuple, dict]] = []

    def print(self, *args, **kwargs):
        self.print_calls.append((args, kwargs))


class TestGetInstallId:
    def test_creates_install_id_on_first_call(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from basic_memory.telemetry import get_install_id

        install_id = get_install_id()
        assert len(install_id) == 36
        assert install_id.count("-") == 4

        id_file = tmp_path / ".basic-memory" / ".install_id"
        assert id_file.exists()
        assert id_file.read_text().strip() == install_id

    def test_returns_existing_install_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        id_file = tmp_path / ".basic-memory" / ".install_id"
        id_file.parent.mkdir(parents=True, exist_ok=True)
        id_file.write_text("test-uuid-12345")

        from basic_memory.telemetry import get_install_id

        assert get_install_id() == "test-uuid-12345"


class TestTelemetryConfig:
    def test_telemetry_enabled_defaults_to_true(self, config_home, monkeypatch):
        import basic_memory.config

        basic_memory.config._CONFIG_CACHE = None
        assert BasicMemoryConfig().telemetry_enabled is True

    def test_telemetry_notice_shown_defaults_to_false(self, config_home, monkeypatch):
        import basic_memory.config

        basic_memory.config._CONFIG_CACHE = None
        assert BasicMemoryConfig().telemetry_notice_shown is False

    def test_telemetry_enabled_via_env_var(self, config_home, monkeypatch):
        import basic_memory.config

        basic_memory.config._CONFIG_CACHE = None
        monkeypatch.setenv("BASIC_MEMORY_TELEMETRY_ENABLED", "false")
        assert BasicMemoryConfig().telemetry_enabled is False


class TestTrack:
    def test_track_does_not_raise_on_error(self, config_home, monkeypatch):
        import basic_memory.telemetry as telemetry
        import basic_memory.config

        basic_memory.config._CONFIG_CACHE = None
        telemetry.reset_client()
        # Force telemetry to be enabled for this test
        monkeypatch.setattr(telemetry, "_is_telemetry_enabled", lambda: True)

        # Replace OpenPanel with a stub that raises on track
        stub_client = _StubOpenPanel(client_id="id", client_secret="sec", disabled=False)
        stub_client.raise_on_track = Exception("Network error")

        def openpanel_factory(*, client_id, client_secret):
            stub_client.client_id = client_id
            stub_client.client_secret = client_secret
            return stub_client

        # Mock the import inside _get_client()
        monkeypatch.setattr("openpanel.OpenPanel", openpanel_factory)

        # Should not raise
        telemetry.track("test_event", {"key": "value"})

    def test_track_respects_disabled_config(self, config_home, monkeypatch):
        import basic_memory.telemetry as telemetry
        import basic_memory.config

        basic_memory.config._CONFIG_CACHE = None
        telemetry.reset_client()

        monkeypatch.setenv("BASIC_MEMORY_TELEMETRY_ENABLED", "false")

        created: list[_StubOpenPanel] = []

        def openpanel_factory(*, client_id, client_secret):
            client = _StubOpenPanel(
                client_id=client_id, client_secret=client_secret, disabled=False
            )
            created.append(client)
            return client

        monkeypatch.setattr("openpanel.OpenPanel", openpanel_factory)

        telemetry.track("test_event")
        # With disabled config, OpenPanel client is never created (early return)
        assert len(created) == 0


class TestShowNoticeIfNeeded:
    def test_shows_notice_when_enabled_and_not_shown(self, config_manager, monkeypatch):
        import basic_memory.telemetry as telemetry

        telemetry.reset_client()

        # Ensure config state: enabled + not yet shown
        cfg = config_manager.load_config()
        cfg.telemetry_enabled = True
        cfg.telemetry_notice_shown = False
        config_manager.save_config(cfg)

        instances: list[_StubConsole] = []

        def console_factory(*_args, **_kwargs):
            c = _StubConsole()
            instances.append(c)
            return c

        monkeypatch.setattr("rich.console.Console", console_factory)

        telemetry.show_notice_if_needed()

        assert len(instances) == 1
        assert len(instances[0].print_calls) == 1

        cfg2 = config_manager.load_config()
        assert cfg2.telemetry_notice_shown is True

    def test_does_not_show_notice_when_disabled(self, config_manager, monkeypatch):
        import basic_memory.telemetry as telemetry

        telemetry.reset_client()

        cfg = config_manager.load_config()
        cfg.telemetry_enabled = False
        cfg.telemetry_notice_shown = False
        config_manager.save_config(cfg)

        def console_factory(*_args, **_kwargs):
            raise AssertionError("Console should not be constructed when telemetry is disabled")

        monkeypatch.setattr("rich.console.Console", console_factory)

        telemetry.show_notice_if_needed()


class TestConvenienceFunctions:
    def test_track_app_started(self, config_home, monkeypatch):
        import basic_memory.telemetry as telemetry
        import basic_memory.config

        basic_memory.config._CONFIG_CACHE = None
        telemetry.reset_client()
        # Force telemetry to be enabled for this test (pytest sets PYTEST_CURRENT_TEST)
        monkeypatch.setattr(telemetry, "_is_telemetry_enabled", lambda: True)

        created: list[_StubOpenPanel] = []

        def openpanel_factory(*, client_id, client_secret):
            client = _StubOpenPanel(
                client_id=client_id, client_secret=client_secret, disabled=False
            )
            created.append(client)
            return client

        monkeypatch.setattr("openpanel.OpenPanel", openpanel_factory)

        telemetry.track_app_started("cli")
        assert created
        assert created[0].events[-1] == ("app_started", {"mode": "cli"})

    def test_track_mcp_tool(self, config_home, monkeypatch):
        import basic_memory.telemetry as telemetry
        import basic_memory.config

        basic_memory.config._CONFIG_CACHE = None
        telemetry.reset_client()
        # Force telemetry to be enabled for this test
        monkeypatch.setattr(telemetry, "_is_telemetry_enabled", lambda: True)

        created: list[_StubOpenPanel] = []

        def openpanel_factory(*, client_id, client_secret):
            client = _StubOpenPanel(
                client_id=client_id, client_secret=client_secret, disabled=False
            )
            created.append(client)
            return client

        monkeypatch.setattr("openpanel.OpenPanel", openpanel_factory)

        telemetry.track_mcp_tool("write_note")
        assert created
        assert created[0].events[-1] == ("mcp_tool_called", {"tool": "write_note"})

    def test_track_error_truncates_message(self, config_home, monkeypatch):
        import basic_memory.telemetry as telemetry
        import basic_memory.config

        basic_memory.config._CONFIG_CACHE = None
        telemetry.reset_client()
        # Force telemetry to be enabled for this test
        monkeypatch.setattr(telemetry, "_is_telemetry_enabled", lambda: True)

        created: list[_StubOpenPanel] = []

        def openpanel_factory(*, client_id, client_secret):
            client = _StubOpenPanel(
                client_id=client_id, client_secret=client_secret, disabled=False
            )
            created.append(client)
            return client

        monkeypatch.setattr("openpanel.OpenPanel", openpanel_factory)

        telemetry.track_error("ValueError", "x" * 500)
        _, props = created[0].events[-1]
        assert len(props["message"]) == 200

    def test_track_error_sanitizes_file_paths(self, config_home, monkeypatch):
        import basic_memory.telemetry as telemetry
        import basic_memory.config

        basic_memory.config._CONFIG_CACHE = None
        telemetry.reset_client()
        # Force telemetry to be enabled for this test
        monkeypatch.setattr(telemetry, "_is_telemetry_enabled", lambda: True)

        created: list[_StubOpenPanel] = []

        def openpanel_factory(*, client_id, client_secret):
            client = _StubOpenPanel(
                client_id=client_id, client_secret=client_secret, disabled=False
            )
            created.append(client)
            return client

        monkeypatch.setattr("openpanel.OpenPanel", openpanel_factory)

        telemetry.track_error("FileNotFoundError", "No such file: /Users/john/notes/secret.md")
        _, props = created[0].events[-1]
        assert "/Users/john" not in props["message"]
        assert "[FILE]" in props["message"]

        telemetry.reset_client()
        created.clear()

        monkeypatch.setattr("openpanel.OpenPanel", openpanel_factory)
        telemetry.track_error("FileNotFoundError", "Cannot open C:\\Users\\john\\docs\\private.txt")
        _, props = created[0].events[-1]
        assert "C:\\Users\\john" not in props["message"]
        assert "[FILE]" in props["message"]
