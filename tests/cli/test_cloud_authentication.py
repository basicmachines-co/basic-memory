"""Tests for cloud authentication and subscription validation."""

from __future__ import annotations

from contextlib import asynccontextmanager

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
                auth=auth,
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
                auth=auth,
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
                auth=auth,
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

        instances: list[object] = []

        class _StubConfig:
            cloud_mode = False

        class _StubConfigManager:
            def __init__(self):
                self._config = _StubConfig()
                self.config = self._config
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

        result = runner.invoke(app, ["cloud", "login"])
        assert result.exit_code == 0
        assert "Cloud mode enabled" in result.stdout

        assert len(instances) == 1
        mgr = instances[0]
        assert mgr.saved_config is not None
        assert mgr.saved_config.cloud_mode is True

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
