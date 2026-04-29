"""Tests for cloud authentication and subscription validation."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, cast

import httpx
import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.cli.commands.cloud.api_client import (
    CloudAPIError,
    SubscriptionRequiredError,
    make_api_request,
)


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


class TestLogoutCommand:
    """Tests for `bm cloud logout`."""

    @staticmethod
    def _patch(monkeypatch, default_workspace):
        class FakeConfig:
            cloud_client_id = "cid"
            cloud_domain = "https://auth.example.com"

            def __init__(self):
                self.default_workspace = default_workspace

        saved: list[FakeConfig] = []
        config_instance = FakeConfig()
        logout_called = {"value": False}

        class FakeConfigManager:
            config = config_instance

            def save_config(self, cfg):
                saved.append(cfg)

        class FakeAuth:
            def __init__(self, **_kwargs):
                pass

            def logout(self):
                logout_called["value"] = True

        monkeypatch.setattr(
            "basic_memory.cli.commands.cloud.core_commands.ConfigManager", FakeConfigManager
        )
        monkeypatch.setattr("basic_memory.cli.commands.cloud.core_commands.CLIAuth", FakeAuth)
        return config_instance, saved, logout_called

    def test_logout_clears_default_workspace(self, monkeypatch):
        """Regression for #755: logout must invalidate the cached workspace."""
        config_instance, saved, logout_called = self._patch(
            monkeypatch, default_workspace="tenant-org-123"
        )
        runner = CliRunner()

        result = runner.invoke(app, ["cloud", "logout"])

        assert result.exit_code == 0
        assert logout_called["value"] is True
        assert config_instance.default_workspace is None
        # Save was called once because a non-None value needed clearing.
        assert len(saved) == 1
        assert saved[0].default_workspace is None

    def test_logout_skips_save_when_no_default_workspace(self, monkeypatch):
        """If nothing was cached, logout shouldn't rewrite the config file."""
        config_instance, saved, logout_called = self._patch(monkeypatch, default_workspace=None)
        runner = CliRunner()

        result = runner.invoke(app, ["cloud", "logout"])

        assert result.exit_code == 0
        assert logout_called["value"] is True
        assert config_instance.default_workspace is None
        # No save: avoid touching the file when there's nothing to clear.
        assert saved == []
