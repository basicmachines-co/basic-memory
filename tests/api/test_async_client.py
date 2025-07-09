"""Tests for async_client configuration."""

from unittest.mock import patch
from httpx import AsyncClient, ASGITransport

from basic_memory.mcp.async_client import create_client
from basic_memory.config import BasicMemoryConfig


def test_create_client_uses_asgi_when_no_api_url():
    """Test that create_client uses ASGI transport when api_url is None."""
    mock_config = BasicMemoryConfig(api_url=None)

    with patch("basic_memory.mcp.async_client.ConfigManager") as mock_config_manager:
        mock_config_manager.return_value.load_config.return_value = mock_config

        client = create_client()

        assert isinstance(client, AsyncClient)
        assert isinstance(client._transport, ASGITransport)
        assert str(client.base_url) == "http://test"


def test_create_client_uses_http_when_api_url_set():
    """Test that create_client uses HTTP transport when api_url is configured."""
    remote_url = "https://api.basicmemory.example.com"
    mock_config = BasicMemoryConfig(api_url=remote_url)

    with patch("basic_memory.mcp.async_client.ConfigManager") as mock_config_manager:
        mock_config_manager.return_value.load_config.return_value = mock_config

        client = create_client()

        assert isinstance(client, AsyncClient)
        assert not isinstance(client._transport, ASGITransport)
        assert str(client.base_url) == remote_url
