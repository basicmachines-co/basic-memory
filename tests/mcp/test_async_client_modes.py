from contextlib import asynccontextmanager

import httpx
import pytest

from basic_memory.cli.auth import CLIAuth
from basic_memory.config import ProjectMode
from basic_memory.mcp import async_client as async_client_module
from basic_memory.mcp.async_client import get_client, set_client_factory


@pytest.fixture(autouse=True)
def _reset_async_client_factory():
    async_client_module._client_factory = None
    yield
    async_client_module._client_factory = None


@pytest.mark.asyncio
async def test_get_client_uses_injected_factory(monkeypatch):
    seen = {"used": False}

    @asynccontextmanager
    async def factory():
        seen["used"] = True
        async with httpx.AsyncClient(base_url="https://example.test") as client:
            yield client

    # Ensure we don't leak factory to other tests
    set_client_factory(factory)
    async with get_client() as client:
        assert str(client.base_url) == "https://example.test"
    assert seen["used"] is True


@pytest.mark.asyncio
async def test_get_client_cloud_mode_injects_auth_header(config_manager, config_home):
    cfg = config_manager.load_config()
    cfg.cloud_mode = True
    cfg.cloud_host = "https://cloud.example.test"
    cfg.cloud_client_id = "cid"
    cfg.cloud_domain = "https://auth.example.test"
    config_manager.save_config(cfg)

    # Write token for CLIAuth so get_client() can authenticate without network
    auth = CLIAuth(client_id=cfg.cloud_client_id, authkit_domain=cfg.cloud_domain)
    auth.token_file.parent.mkdir(parents=True, exist_ok=True)
    auth.token_file.write_text(
        '{"access_token":"token-123","refresh_token":null,"expires_at":9999999999,"token_type":"Bearer"}',
        encoding="utf-8",
    )

    async with get_client() as client:
        assert str(client.base_url).rstrip("/") == "https://cloud.example.test/proxy"
        assert client.headers.get("Authorization") == "Bearer token-123"


@pytest.mark.asyncio
async def test_get_client_cloud_mode_raises_when_not_authenticated(config_manager):
    cfg = config_manager.load_config()
    cfg.cloud_mode = True
    cfg.cloud_host = "https://cloud.example.test"
    cfg.cloud_client_id = "cid"
    cfg.cloud_domain = "https://auth.example.test"
    config_manager.save_config(cfg)

    # No token file written -> should raise
    with pytest.raises(RuntimeError, match="Cloud mode enabled but not authenticated"):
        async with get_client():
            pass


@pytest.mark.asyncio
async def test_get_client_local_mode_uses_asgi_transport(config_manager):
    cfg = config_manager.load_config()
    cfg.cloud_mode = False
    config_manager.save_config(cfg)

    async with get_client() as client:
        # httpx stores ASGITransport privately, but we can still sanity-check type
        assert isinstance(client._transport, httpx.ASGITransport)  # pyright: ignore[reportPrivateUsage]


# --- Per-project cloud routing tests ---


@pytest.mark.asyncio
async def test_get_client_per_project_cloud_mode_uses_api_key(config_manager, config_home):
    """Test that a cloud-mode project routes through cloud with API key auth."""
    cfg = config_manager.load_config()
    cfg.cloud_mode = False  # Global cloud mode off
    cfg.cloud_host = "https://cloud.example.test"
    cfg.cloud_api_key = "bmc_test_key_123"
    cfg.set_project_mode("research", ProjectMode.CLOUD)
    config_manager.save_config(cfg)

    async with get_client(project_name="research") as client:
        assert str(client.base_url).rstrip("/") == "https://cloud.example.test/proxy"
        assert client.headers.get("Authorization") == "Bearer bmc_test_key_123"


@pytest.mark.asyncio
async def test_get_client_per_project_cloud_mode_raises_without_api_key(
    config_manager, config_home
):
    """Test that a cloud-mode project raises error when no API key is configured."""
    cfg = config_manager.load_config()
    cfg.cloud_mode = False
    cfg.cloud_api_key = None  # No API key
    cfg.set_project_mode("research", ProjectMode.CLOUD)
    config_manager.save_config(cfg)

    with pytest.raises(
        RuntimeError,
        match="Project 'research' is set to cloud mode but no API key configured",
    ):
        async with get_client(project_name="research"):
            pass


@pytest.mark.asyncio
async def test_get_client_local_project_uses_asgi_transport(config_manager, config_home):
    """Test that a local-mode project uses ASGI transport even when API key exists."""
    cfg = config_manager.load_config()
    cfg.cloud_mode = False
    cfg.cloud_api_key = "bmc_test_key_123"
    # "main" defaults to LOCAL since we didn't set_project_mode
    config_manager.save_config(cfg)

    async with get_client(project_name="main") as client:
        assert isinstance(client._transport, httpx.ASGITransport)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_get_client_no_project_name_uses_default_routing(config_manager, config_home):
    """Test that get_client without project_name falls through to default routing."""
    cfg = config_manager.load_config()
    cfg.cloud_mode = False
    cfg.cloud_api_key = "bmc_test_key_123"
    cfg.set_project_mode("research", ProjectMode.CLOUD)
    config_manager.save_config(cfg)

    # No project_name → should use local ASGI transport (cloud_mode is False)
    async with get_client() as client:
        assert isinstance(client._transport, httpx.ASGITransport)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_get_client_factory_overrides_per_project_routing(config_manager, config_home):
    """Test that injected factory takes priority over per-project routing."""
    cfg = config_manager.load_config()
    cfg.cloud_api_key = "bmc_test_key_123"
    cfg.set_project_mode("research", ProjectMode.CLOUD)
    config_manager.save_config(cfg)

    @asynccontextmanager
    async def factory():
        async with httpx.AsyncClient(base_url="https://factory.test") as client:
            yield client

    set_client_factory(factory)

    # Even though project is CLOUD, factory should take priority
    async with get_client(project_name="research") as client:
        assert str(client.base_url) == "https://factory.test"
