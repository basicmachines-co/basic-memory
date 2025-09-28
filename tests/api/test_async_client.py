"""Tests for async_client configuration."""

from unittest.mock import patch
from httpx import AsyncClient, ASGITransport, Timeout

from basic_memory.mcp.async_client import create_client


def test_create_client_uses_asgi_when_no_remote_env():
    """Test that create_client uses ASGI transport when BASIC_MEMORY_USE_REMOTE_API is not set."""
    with patch.dict("os.environ", {}, clear=True):
        client = create_client()

        assert isinstance(client, AsyncClient)
        assert isinstance(client._transport, ASGITransport)
        assert str(client.base_url) == "http://test"


def test_create_client_uses_http_when_proxy_env_set():
    """Test that create_client uses HTTP transport when BASIC_MEMORY_USE_REMOTE_API is set."""
    with patch.dict("os.environ", {"BASIC_MEMORY_PROXY_URL": "http://localhost:8000"}):
        client = create_client()

        assert isinstance(client, AsyncClient)
        assert not isinstance(client._transport, ASGITransport)
        # When using remote API, no base_url is set (dynamic from headers)
        assert str(client.base_url) == "http://localhost:8000"


def test_create_client_configures_extended_timeouts():
    """Test that create_client configures 30-second timeouts for long operations."""
    with patch.dict("os.environ", {}, clear=True):
        client = create_client()

        # Verify timeout configuration
        assert isinstance(client.timeout, Timeout)
        assert client.timeout.connect == 10.0  # 10 seconds for connection
        assert client.timeout.read == 30.0     # 30 seconds for reading
        assert client.timeout.write == 30.0    # 30 seconds for writing
        assert client.timeout.pool == 30.0     # 30 seconds for pool

    # Also test with proxy URL
    with patch.dict("os.environ", {"BASIC_MEMORY_PROXY_URL": "http://localhost:8000"}):
        client = create_client()

        # Same timeout configuration should apply
        assert isinstance(client.timeout, Timeout)
        assert client.timeout.read == 30.0
        assert client.timeout.write == 30.0
