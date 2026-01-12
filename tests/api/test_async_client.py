"""Tests for async_client configuration."""

from httpx import AsyncClient, ASGITransport, Timeout

from basic_memory.mcp.async_client import create_client


def test_create_client_uses_asgi_when_no_remote_env(config_manager, monkeypatch):
    """Test that create_client uses ASGI transport when cloud mode is disabled."""
    monkeypatch.delenv("BASIC_MEMORY_USE_REMOTE_API", raising=False)
    monkeypatch.delenv("BASIC_MEMORY_CLOUD_MODE", raising=False)

    cfg = config_manager.load_config()
    cfg.cloud_mode = False
    config_manager.save_config(cfg)

    client = create_client()

    assert isinstance(client, AsyncClient)
    assert isinstance(client._transport, ASGITransport)
    assert str(client.base_url) == "http://test"


def test_create_client_uses_http_when_cloud_mode_env_set(config_manager, monkeypatch):
    """Test that create_client uses HTTP transport when BASIC_MEMORY_CLOUD_MODE is set."""
    monkeypatch.setenv("BASIC_MEMORY_CLOUD_MODE", "True")

    config = config_manager.load_config()
    client = create_client()

    assert isinstance(client, AsyncClient)
    assert not isinstance(client._transport, ASGITransport)
    # Cloud mode uses cloud_host/proxy as base_url
    assert str(client.base_url) == f"{config.cloud_host}/proxy/"


def test_create_client_configures_extended_timeouts(config_manager, monkeypatch):
    """Test that create_client configures 30-second timeouts for long operations."""
    monkeypatch.delenv("BASIC_MEMORY_USE_REMOTE_API", raising=False)
    monkeypatch.delenv("BASIC_MEMORY_CLOUD_MODE", raising=False)

    cfg = config_manager.load_config()
    cfg.cloud_mode = False
    config_manager.save_config(cfg)

    client = create_client()

    # Verify timeout configuration
    assert isinstance(client.timeout, Timeout)
    assert client.timeout.connect == 10.0  # 10 seconds for connection
    assert client.timeout.read == 30.0  # 30 seconds for reading
    assert client.timeout.write == 30.0  # 30 seconds for writing
    assert client.timeout.pool == 30.0  # 30 seconds for pool
