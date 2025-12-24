"""Tests for async_client configuration."""

import os
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport, Timeout

from basic_memory.config import ConfigManager
from basic_memory.mcp.async_client import create_client


def test_create_client_uses_asgi_when_no_remote_env():
    """Test that create_client uses ASGI transport when cloud mode is disabled."""
    # Ensure env vars are not set and config cloud_mode is False
    with patch.dict("os.environ", clear=False):
        os.environ.pop("BASIC_MEMORY_USE_REMOTE_API", None)
        os.environ.pop("BASIC_MEMORY_CLOUD_MODE", None)

        # Also patch the config's cloud_mode to ensure it's False
        with patch.object(ConfigManager().config, "cloud_mode", False):
            client = create_client()

            assert isinstance(client, AsyncClient)
            assert isinstance(client._transport, ASGITransport)
            assert str(client.base_url) == "http://test"


def test_create_client_uses_http_when_cloud_mode_env_set():
    """Test that create_client uses HTTP transport when BASIC_MEMORY_CLOUD_MODE is set."""

    config = ConfigManager().config
    with patch.dict("os.environ", {"BASIC_MEMORY_CLOUD_MODE": "True"}):
        client = create_client()

        assert isinstance(client, AsyncClient)
        assert not isinstance(client._transport, ASGITransport)
        # Cloud mode uses cloud_host/proxy as base_url
        assert str(client.base_url) == f"{config.cloud_host}/proxy/"


def test_create_client_configures_extended_timeouts():
    """Test that create_client configures 30-second timeouts for long operations."""
    # Ensure env vars are not set and config cloud_mode is False
    with patch.dict("os.environ", clear=False):
        os.environ.pop("BASIC_MEMORY_USE_REMOTE_API", None)
        os.environ.pop("BASIC_MEMORY_CLOUD_MODE", None)

        # Also patch the config's cloud_mode to ensure it's False
        with patch.object(ConfigManager().config, "cloud_mode", False):
            client = create_client()

            # Verify timeout configuration
            assert isinstance(client.timeout, Timeout)
            assert client.timeout.connect == 10.0  # 10 seconds for connection
            assert client.timeout.read == 30.0  # 30 seconds for reading
            assert client.timeout.write == 30.0  # 30 seconds for writing
            assert client.timeout.pool == 30.0  # 30 seconds for pool
