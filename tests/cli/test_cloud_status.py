"""Tests for cloud status command."""

from __future__ import annotations

import time

import httpx
import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.cli.commands.cloud.api_client import CloudAPIError


# --- status command integration tests ---


class _FakeTokens:
    """Provides canned token data for CLIAuth stubs."""

    @classmethod
    def valid(cls) -> dict:
        return {
            "access_token": "fake-access-token",
            "refresh_token": "rt_test",
            "expires_at": int(time.time()) + 3600,
        }

    @classmethod
    def expired(cls) -> dict:
        return {
            "access_token": "fake-access-token",
            "refresh_token": "rt_test",
            "expires_at": int(time.time()) - 3600,
        }


def _patch_status_deps(monkeypatch, *, tokens=None, api_side_effect=None):
    """Patch ConfigManager and CLIAuth for the status command."""

    class FakeConfig:
        cloud_client_id = "cid"
        cloud_domain = "https://auth.example.com"
        cloud_host = "https://cloud.example.com"
        cloud_api_key = "bmc_test123"

    class FakeConfigManager:
        config = FakeConfig()

        def load_config(self):
            return self.config

    class FakeAuth:
        def __init__(self, **_kwargs):
            pass

        def load_tokens(self):
            return tokens

        def is_token_valid(self, t):
            return t.get("expires_at", 0) > time.time()

    monkeypatch.setattr(
        "basic_memory.cli.commands.cloud.core_commands.ConfigManager", FakeConfigManager
    )
    monkeypatch.setattr(
        "basic_memory.cli.commands.cloud.core_commands.CLIAuth", FakeAuth
    )
    monkeypatch.setattr(
        "basic_memory.cli.commands.cloud.core_commands.get_cloud_config",
        lambda: ("cid", "domain", "https://cloud.example.com"),
    )

    if api_side_effect is None:
        # Default: cloud is reachable
        async def _ok(*_a, **_kw):
            return httpx.Response(200, json={"status": "ok"})

        api_side_effect = _ok

    monkeypatch.setattr(
        "basic_memory.cli.commands.cloud.core_commands.make_api_request", api_side_effect
    )


class TestStatusCommand:
    def test_status_connected(self, monkeypatch):
        _patch_status_deps(monkeypatch, tokens=_FakeTokens.valid())
        runner = CliRunner()
        result = runner.invoke(app, ["cloud", "status"])

        assert result.exit_code == 0
        assert "Cloud Status" in result.stdout
        assert "cloud.example.com" in result.stdout
        assert "token valid" in result.stdout
        assert "Cloud connected" in result.stdout

    def test_status_expired_token(self, monkeypatch):
        _patch_status_deps(monkeypatch, tokens=_FakeTokens.expired())
        runner = CliRunner()
        result = runner.invoke(app, ["cloud", "status"])

        assert result.exit_code == 0
        assert "token expired" in result.stdout

    def test_status_no_credentials(self, monkeypatch):
        _patch_status_deps(monkeypatch, tokens=None)

        # Also clear the API key so there are no credentials at all
        class FakeConfig:
            cloud_client_id = "cid"
            cloud_domain = "https://auth.example.com"
            cloud_host = "https://cloud.example.com"
            cloud_api_key = ""

        class FakeConfigManager:
            config = FakeConfig()

            def load_config(self):
                return self.config

        monkeypatch.setattr(
            "basic_memory.cli.commands.cloud.core_commands.ConfigManager", FakeConfigManager
        )

        runner = CliRunner()
        result = runner.invoke(app, ["cloud", "status"])

        assert result.exit_code == 0
        assert "No cloud credentials found" in result.stdout

    @pytest.mark.parametrize(
        "exc",
        [
            CloudAPIError("connection refused"),
            Exception("network timeout"),
        ],
    )
    def test_status_cloud_not_connected(self, monkeypatch, exc):
        async def _fail(*_a, **_kw):
            raise exc

        _patch_status_deps(monkeypatch, tokens=_FakeTokens.valid(), api_side_effect=_fail)
        runner = CliRunner()
        result = runner.invoke(app, ["cloud", "status"])

        assert result.exit_code == 0
        assert "Cloud not connected" in result.stdout
