from contextlib import asynccontextmanager

import httpx
import pytest

from basic_memory.cli.auth import CLIAuth
from basic_memory.config import ProjectMode
from basic_memory.mcp import async_client as async_client_module
from basic_memory.mcp.async_client import (
    get_client,
    get_cloud_control_plane_client,
    set_client_factory,
)


@pytest.fixture(autouse=True)
def _reset_async_client_state(monkeypatch):
    async_client_module._client_factory = None
    monkeypatch.delenv("BASIC_MEMORY_FORCE_LOCAL", raising=False)
    monkeypatch.delenv("BASIC_MEMORY_FORCE_CLOUD", raising=False)
    monkeypatch.delenv("BASIC_MEMORY_EXPLICIT_ROUTING", raising=False)
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
async def test_get_client_default_uses_local_asgi_transport(config_manager):
    cfg = config_manager.load_config()
    cfg.cloud_host = "https://cloud.example.test"
    cfg.cloud_api_key = "bmc_test_key_123"
    config_manager.save_config(cfg)

    async with get_client() as client:
        assert isinstance(client._transport, httpx.ASGITransport)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_get_client_explicit_cloud_uses_api_key(config_manager, monkeypatch):
    cfg = config_manager.load_config()
    cfg.cloud_host = "https://cloud.example.test"
    cfg.cloud_api_key = "bmc_test_key_123"
    config_manager.save_config(cfg)

    monkeypatch.setenv("BASIC_MEMORY_FORCE_CLOUD", "true")
    monkeypatch.setenv("BASIC_MEMORY_EXPLICIT_ROUTING", "true")

    async with get_client() as client:
        assert str(client.base_url).rstrip("/") == "https://cloud.example.test/proxy"
        assert client.headers.get("Authorization") == "Bearer bmc_test_key_123"


@pytest.mark.asyncio
async def test_get_client_cloud_adds_workspace_header(config_manager):
    cfg = config_manager.load_config()
    cfg.cloud_host = "https://cloud.example.test"
    cfg.cloud_api_key = "bmc_test_key_123"
    cfg.set_project_mode("research", ProjectMode.CLOUD)
    config_manager.save_config(cfg)

    async with get_client(project_name="research", workspace="tenant-123") as client:
        assert str(client.base_url).rstrip("/") == "https://cloud.example.test/proxy"
        assert client.headers.get("X-Workspace-ID") == "tenant-123"


@pytest.mark.asyncio
async def test_get_client_explicit_cloud_raises_without_credentials(config_manager, monkeypatch):
    cfg = config_manager.load_config()
    cfg.cloud_host = "https://cloud.example.test"
    cfg.cloud_api_key = None
    cfg.cloud_client_id = "cid"
    cfg.cloud_domain = "https://auth.example.test"
    config_manager.save_config(cfg)

    monkeypatch.setenv("BASIC_MEMORY_FORCE_CLOUD", "true")
    monkeypatch.setenv("BASIC_MEMORY_EXPLICIT_ROUTING", "true")

    with pytest.raises(RuntimeError, match="Cloud routing requested but no credentials found"):
        async with get_client():
            pass


@pytest.mark.asyncio
async def test_get_client_per_project_cloud_uses_api_key(config_manager):
    """Cloud-mode project routes through cloud with API key auth."""
    cfg = config_manager.load_config()
    cfg.cloud_host = "https://cloud.example.test"
    cfg.cloud_api_key = "bmc_test_key_123"
    cfg.set_project_mode("research", ProjectMode.CLOUD)
    config_manager.save_config(cfg)

    async with get_client(project_name="research") as client:
        assert str(client.base_url).rstrip("/") == "https://cloud.example.test/proxy"
        assert client.headers.get("Authorization") == "Bearer bmc_test_key_123"


@pytest.mark.asyncio
async def test_get_client_per_project_cloud_raises_without_credentials(config_manager):
    """Cloud-mode project raises with actionable auth guidance when no credentials exist."""
    cfg = config_manager.load_config()
    cfg.cloud_api_key = None
    cfg.set_project_mode("research", ProjectMode.CLOUD)
    config_manager.save_config(cfg)

    with pytest.raises(RuntimeError, match="Project 'research' is set to cloud mode"):
        async with get_client(project_name="research"):
            pass


@pytest.mark.asyncio
async def test_get_client_local_project_uses_asgi_transport(config_manager):
    """Local-mode project uses ASGI transport even if API key exists."""
    cfg = config_manager.load_config()
    cfg.cloud_api_key = "bmc_test_key_123"
    config_manager.save_config(cfg)

    async with get_client(project_name="main") as client:
        assert isinstance(client._transport, httpx.ASGITransport)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_get_client_no_project_name_defaults_local(config_manager):
    """No project_name defaults to local ASGI routing."""
    cfg = config_manager.load_config()
    cfg.cloud_api_key = "bmc_test_key_123"
    cfg.set_project_mode("research", ProjectMode.CLOUD)
    config_manager.save_config(cfg)

    async with get_client() as client:
        assert isinstance(client._transport, httpx.ASGITransport)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_get_client_factory_overrides_per_project_routing(config_manager):
    """Injected factory takes priority over per-project routing."""
    cfg = config_manager.load_config()
    cfg.cloud_api_key = "bmc_test_key_123"
    cfg.set_project_mode("research", ProjectMode.CLOUD)
    config_manager.save_config(cfg)

    @asynccontextmanager
    async def factory():
        async with httpx.AsyncClient(base_url="https://factory.test") as client:
            yield client

    set_client_factory(factory)

    async with get_client(project_name="research") as client:
        assert str(client.base_url) == "https://factory.test"


@pytest.mark.asyncio
async def test_get_client_force_local_without_explicit_does_not_override_project_mode(
    config_manager, monkeypatch
):
    """FORCE_LOCAL alone should not bypass per-project cloud routing."""
    cfg = config_manager.load_config()
    cfg.cloud_host = "https://cloud.example.test"
    cfg.cloud_api_key = "bmc_test_key_123"
    cfg.set_project_mode("research", ProjectMode.CLOUD)
    config_manager.save_config(cfg)

    monkeypatch.setenv("BASIC_MEMORY_FORCE_LOCAL", "true")

    async with get_client(project_name="research") as client:
        assert str(client.base_url).rstrip("/") == "https://cloud.example.test/proxy"


@pytest.mark.asyncio
async def test_get_client_explicit_local_overrides_cloud_project(config_manager, monkeypatch):
    """EXPLICIT_ROUTING + FORCE_LOCAL should override a cloud project to local ASGI."""
    cfg = config_manager.load_config()
    cfg.cloud_host = "https://cloud.example.test"
    cfg.cloud_api_key = "bmc_test_key_123"
    cfg.set_project_mode("research", ProjectMode.CLOUD)
    config_manager.save_config(cfg)

    monkeypatch.setenv("BASIC_MEMORY_FORCE_LOCAL", "true")
    monkeypatch.setenv("BASIC_MEMORY_EXPLICIT_ROUTING", "true")

    async with get_client(project_name="research") as client:
        assert isinstance(client._transport, httpx.ASGITransport)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_get_client_per_project_cloud_oauth_fallback(config_manager):
    """Cloud-mode project uses OAuth token when no API key is configured."""
    cfg = config_manager.load_config()
    cfg.cloud_host = "https://cloud.example.test"
    cfg.cloud_api_key = None
    cfg.cloud_client_id = "cid"
    cfg.cloud_domain = "https://auth.example.test"
    cfg.set_project_mode("research", ProjectMode.CLOUD)
    config_manager.save_config(cfg)

    # Write OAuth token file so CLIAuth.get_valid_token() returns it
    auth = CLIAuth(client_id=cfg.cloud_client_id, authkit_domain=cfg.cloud_domain)
    auth.token_file.parent.mkdir(parents=True, exist_ok=True)
    auth.token_file.write_text(
        '{"access_token":"oauth-token-456","refresh_token":null,"expires_at":9999999999,"token_type":"Bearer"}',
        encoding="utf-8",
    )

    async with get_client(project_name="research") as client:
        assert str(client.base_url).rstrip("/") == "https://cloud.example.test/proxy"
        assert client.headers.get("Authorization") == "Bearer oauth-token-456"


@pytest.mark.asyncio
async def test_get_client_explicit_cloud_overrides_local_project(config_manager, monkeypatch):
    """EXPLICIT_ROUTING + FORCE_CLOUD should override a local project to cloud."""
    cfg = config_manager.load_config()
    cfg.cloud_host = "https://cloud.example.test"
    cfg.cloud_api_key = "bmc_test_key_123"
    config_manager.save_config(cfg)

    monkeypatch.setenv("BASIC_MEMORY_FORCE_CLOUD", "true")
    monkeypatch.setenv("BASIC_MEMORY_EXPLICIT_ROUTING", "true")

    async with get_client(project_name="main") as client:
        assert str(client.base_url).rstrip("/") == "https://cloud.example.test/proxy"
        assert client.headers.get("Authorization") == "Bearer bmc_test_key_123"


@pytest.mark.asyncio
async def test_get_cloud_control_plane_client_requires_oauth(config_manager):
    cfg = config_manager.load_config()
    cfg.cloud_host = "https://cloud.example.test"
    cfg.cloud_api_key = "bmc_test_key_123"
    cfg.cloud_client_id = "cid"
    cfg.cloud_domain = "https://auth.example.test"
    config_manager.save_config(cfg)

    with pytest.raises(RuntimeError, match="Workspace discovery requires OAuth login"):
        async with get_cloud_control_plane_client():
            pass


@pytest.mark.asyncio
async def test_get_cloud_control_plane_client_uses_oauth_token(config_manager):
    cfg = config_manager.load_config()
    cfg.cloud_host = "https://cloud.example.test"
    cfg.cloud_api_key = "bmc_test_key_123"
    cfg.cloud_client_id = "cid"
    cfg.cloud_domain = "https://auth.example.test"
    config_manager.save_config(cfg)

    auth = CLIAuth(client_id=cfg.cloud_client_id, authkit_domain=cfg.cloud_domain)
    auth.token_file.parent.mkdir(parents=True, exist_ok=True)
    auth.token_file.write_text(
        '{"access_token":"oauth-control-123","refresh_token":null,"expires_at":9999999999,"token_type":"Bearer"}',
        encoding="utf-8",
    )

    async with get_cloud_control_plane_client() as client:
        assert str(client.base_url).rstrip("/") == "https://cloud.example.test"
        assert client.headers.get("Authorization") == "Bearer oauth-control-123"
