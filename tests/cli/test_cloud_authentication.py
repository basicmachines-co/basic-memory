"""Tests for cloud authentication and subscription validation."""

from __future__ import annotations

import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from typer.testing import CliRunner

import basic_memory.config
import basic_memory.cli.commands.cloud.core_commands as core_cmd
from basic_memory.cli.app import app
from basic_memory.cli.commands.cloud.api_client import (
    CloudAPIError,
    SubscriptionRequiredError,
    make_api_request,
)
from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.schemas.cloud import WorkspaceInfo


class _StubAuth:
    def __init__(self, token: str = "test-token", login_ok: bool = True):
        self._token = token
        self._login_ok = login_ok

    async def get_valid_token(self) -> str:
        return self._token

    async def login(self) -> bool:
        return self._login_ok


def _auth(auth: _StubAuth) -> Any:
    return cast(Any, auth)


def _make_http_client_factory(handler):
    @asynccontextmanager
    async def _factory():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            yield client

    return _factory


class TestAPIClientErrorHandling:
    """Tests for API client error handling."""

    @pytest.mark.asyncio
    async def test_parse_subscription_required_error(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                json={
                    "detail": {
                        "error": "subscription_required",
                        "message": "Active subscription required for CLI access",
                        "subscribe_url": "https://basicmemory.com/subscribe",
                    }
                },
                request=request,
            )

        auth = _StubAuth()
        with pytest.raises(SubscriptionRequiredError) as exc_info:
            await make_api_request(
                "GET",
                "https://test.com/api/endpoint",
                auth=_auth(auth),
                http_client_factory=_make_http_client_factory(handler),
            )

        err = exc_info.value
        assert err.status_code == 403
        assert err.subscribe_url == "https://basicmemory.com/subscribe"
        assert "Active subscription required" in str(err)

    @pytest.mark.asyncio
    async def test_parse_subscription_required_error_flat_format(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                json={
                    "error": "subscription_required",
                    "message": "Active subscription required",
                    "subscribe_url": "https://basicmemory.com/subscribe",
                },
                request=request,
            )

        auth = _StubAuth()
        with pytest.raises(SubscriptionRequiredError) as exc_info:
            await make_api_request(
                "GET",
                "https://test.com/api/endpoint",
                auth=_auth(auth),
                http_client_factory=_make_http_client_factory(handler),
            )

        err = exc_info.value
        assert err.status_code == 403
        assert err.subscribe_url == "https://basicmemory.com/subscribe"

    @pytest.mark.asyncio
    async def test_parse_generic_403_error(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                json={"error": "forbidden", "message": "Access denied"},
                request=request,
            )

        auth = _StubAuth()
        with pytest.raises(CloudAPIError) as exc_info:
            await make_api_request(
                "GET",
                "https://test.com/api/endpoint",
                auth=_auth(auth),
                http_client_factory=_make_http_client_factory(handler),
            )

        err = exc_info.value
        assert not isinstance(err, SubscriptionRequiredError)
        assert err.status_code == 403


class TestLoginCommand:
    """Tests for cloud login command with subscription validation."""

    def test_login_without_subscription_shows_error(self, monkeypatch):
        runner = CliRunner()

        # Stub auth object returned by CLIAuth(...)
        monkeypatch.setattr(
            "basic_memory.cli.commands.cloud.core_commands.CLIAuth",
            lambda **_kwargs: _StubAuth(login_ok=True),
        )
        monkeypatch.setattr(
            "basic_memory.cli.commands.cloud.core_commands.get_cloud_config",
            lambda: ("client_id", "domain", "https://cloud.example.com"),
        )

        async def fake_make_api_request(*_args, **_kwargs):
            raise SubscriptionRequiredError(
                message="Active subscription required for CLI access",
                subscribe_url="https://basicmemory.com/subscribe",
            )

        monkeypatch.setattr(
            "basic_memory.cli.commands.cloud.core_commands.make_api_request",
            fake_make_api_request,
        )

        result = runner.invoke(app, ["cloud", "login"])
        assert result.exit_code == 1
        assert "Subscription Required" in result.stdout
        assert "Active subscription required" in result.stdout
        assert "https://basicmemory.com/subscribe" in result.stdout
        assert "bm cloud login" in result.stdout

    def test_login_with_subscription_succeeds(self, monkeypatch):
        runner = CliRunner()

        monkeypatch.setattr(
            "basic_memory.cli.commands.cloud.core_commands.CLIAuth",
            lambda **_kwargs: _StubAuth(login_ok=True),
        )
        monkeypatch.setattr(
            "basic_memory.cli.commands.cloud.core_commands.get_cloud_config",
            lambda: ("client_id", "domain", "https://cloud.example.com"),
        )

        async def fake_make_api_request(*_args, **_kwargs):
            # Response is only used for status validation in login().
            return httpx.Response(200, json={"status": "healthy"})

        monkeypatch.setattr(
            "basic_memory.cli.commands.cloud.core_commands.make_api_request",
            fake_make_api_request,
        )

        result = runner.invoke(app, ["cloud", "login"])
        assert result.exit_code == 0
        assert "Cloud authentication successful" in result.stdout
        assert "Cloud host ready: https://cloud.example.com" in result.stdout

    def test_login_authentication_failure(self, monkeypatch):
        runner = CliRunner()

        monkeypatch.setattr(
            "basic_memory.cli.commands.cloud.core_commands.CLIAuth",
            lambda **_kwargs: _StubAuth(login_ok=False),
        )
        monkeypatch.setattr(
            "basic_memory.cli.commands.cloud.core_commands.get_cloud_config",
            lambda: ("client_id", "domain", "https://cloud.example.com"),
        )

        result = runner.invoke(app, ["cloud", "login"])
        assert result.exit_code == 1
        assert "Login failed" in result.stdout


# ---------------------------------------------------------------------------
# Shared config fixture for tests that inspect persisted config state
# ---------------------------------------------------------------------------


class _ConfigFixtureMixin:
    """Sets up an isolated temp config directory for each test."""

    @pytest.fixture(autouse=True)
    def _setup_config(self, monkeypatch):
        self.temp_dir = tempfile.mkdtemp()
        temp_path = Path(self.temp_dir)
        config_dir = temp_path / ".basic-memory"
        config_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HOME", str(temp_path))
        monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(config_dir))
        basic_memory.config._CONFIG_CACHE = None
        basic_memory.config._CONFIG_MTIME = None
        basic_memory.config._CONFIG_SIZE = None
        self.config_manager = ConfigManager()
        self.temp_path = temp_path

    def _reset_cache(self):
        basic_memory.config._CONFIG_CACHE = None
        basic_memory.config._CONFIG_MTIME = None
        basic_memory.config._CONFIG_SIZE = None

    def _save_config(self, **kwargs):
        cfg = BasicMemoryConfig(
            projects={"main": {"path": str(self.temp_path / "main")}},
            **kwargs,
        )
        self.config_manager.save_config(cfg)
        self._reset_cache()


class _FakeAuthFactory:
    """Produces a CLIAuth-compatible stub that never touches the filesystem."""

    def __init__(self, login_ok: bool = True):
        self._login_ok = login_ok

    def __call__(self, **_kwargs):
        login_ok = self._login_ok

        class _Auth:
            async def login(self) -> bool:
                return login_ok

            def logout(self) -> None:
                pass

        return _Auth()


class TestLogoutCommand(_ConfigFixtureMixin):
    """Tests for 'bm cloud logout' — token clearing and workspace reset."""

    def test_logout_clears_default_workspace(self, monkeypatch):
        self._save_config(default_workspace="11111111-1111-1111-1111-111111111111")

        monkeypatch.setattr(core_cmd, "CLIAuth", _FakeAuthFactory())

        runner = CliRunner()
        result = runner.invoke(app, ["cloud", "logout"])
        assert result.exit_code == 0

        self._reset_cache()
        config = ConfigManager().config
        assert config.default_workspace is None

    def test_logout_when_no_default_workspace(self, monkeypatch):
        self._save_config()

        monkeypatch.setattr(core_cmd, "CLIAuth", _FakeAuthFactory())

        runner = CliRunner()
        result = runner.invoke(app, ["cloud", "logout"])
        assert result.exit_code == 0

        self._reset_cache()
        config = ConfigManager().config
        assert config.default_workspace is None


class TestLoginWorkspaceSelection(_ConfigFixtureMixin):
    """Tests for workspace selection step inside 'bm cloud login'."""

    def _patch_login_deps(self, monkeypatch, workspaces, login_ok=True):
        """Patch all login dependencies for a successful login scenario."""
        self._save_config()

        monkeypatch.setattr(core_cmd, "CLIAuth", _FakeAuthFactory(login_ok=login_ok))
        monkeypatch.setattr(
            "basic_memory.cli.commands.cloud.core_commands.get_cloud_config",
            lambda: ("client_id", "domain", "https://cloud.example.com"),
        )

        async def fake_make_api_request(*_args, **_kwargs):
            return httpx.Response(200, json={"status": "healthy"})

        monkeypatch.setattr(core_cmd, "make_api_request", fake_make_api_request)

        async def fake_get_workspaces(context=None):
            return workspaces

        monkeypatch.setattr(core_cmd, "get_available_workspaces", fake_get_workspaces)

    def test_login_single_workspace_auto_sets_default(self, monkeypatch):
        ws = WorkspaceInfo(
            tenant_id="aaaa-1111",
            workspace_type="personal",
            name="Personal",
            role="owner",
        )
        self._patch_login_deps(monkeypatch, [ws])

        runner = CliRunner()
        result = runner.invoke(app, ["cloud", "login"])
        assert result.exit_code == 0
        assert "Personal" in result.stdout

        self._reset_cache()
        config = ConfigManager().config
        assert config.default_workspace == "aaaa-1111"

    def test_login_multiple_workspaces_user_selects(self, monkeypatch):
        ws1 = WorkspaceInfo(
            tenant_id="aaaa-1111",
            workspace_type="personal",
            name="Personal",
            role="owner",
        )
        ws2 = WorkspaceInfo(
            tenant_id="bbbb-2222",
            workspace_type="organization",
            name="Team",
            role="editor",
        )
        self._patch_login_deps(monkeypatch, [ws1, ws2])

        runner = CliRunner()
        # User types "2" to select the second workspace
        result = runner.invoke(app, ["cloud", "login"], input="2\n")
        assert result.exit_code == 0
        assert "Team" in result.stdout

        self._reset_cache()
        config = ConfigManager().config
        assert config.default_workspace == "bbbb-2222"

    def test_login_multiple_workspaces_user_skips(self, monkeypatch):
        ws1 = WorkspaceInfo(
            tenant_id="aaaa-1111",
            workspace_type="personal",
            name="Personal",
            role="owner",
        )
        ws2 = WorkspaceInfo(
            tenant_id="bbbb-2222",
            workspace_type="organization",
            name="Team",
            role="editor",
        )
        self._patch_login_deps(monkeypatch, [ws1, ws2])

        runner = CliRunner()
        # User presses Enter to skip selection
        result = runner.invoke(app, ["cloud", "login"], input="\n")
        assert result.exit_code == 0
        assert "bm cloud workspace set-default" in result.stdout

        self._reset_cache()
        config = ConfigManager().config
        # No workspace should be auto-set
        assert config.default_workspace is None

    def test_login_workspace_discovery_failure_is_nonfatal(self, monkeypatch):
        self._save_config()

        monkeypatch.setattr(core_cmd, "CLIAuth", _FakeAuthFactory())
        monkeypatch.setattr(
            "basic_memory.cli.commands.cloud.core_commands.get_cloud_config",
            lambda: ("client_id", "domain", "https://cloud.example.com"),
        )

        async def fake_make_api_request(*_args, **_kwargs):
            return httpx.Response(200, json={"status": "healthy"})

        monkeypatch.setattr(core_cmd, "make_api_request", fake_make_api_request)

        async def fail_get_workspaces(context=None):
            raise RuntimeError("no connection")

        monkeypatch.setattr(core_cmd, "get_available_workspaces", fail_get_workspaces)

        runner = CliRunner()
        result = runner.invoke(app, ["cloud", "login"])
        # Login should still succeed despite workspace discovery failure
        assert result.exit_code == 0
        assert "Cloud authentication successful" in result.stdout
        assert "Workspace discovery unavailable" in result.stdout
