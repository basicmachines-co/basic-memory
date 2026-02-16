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
async def test_get_client_per_project_cloud_mode_raises_without_credentials(
    config_manager, config_home
):
    """Test that a cloud-mode project raises error when no credentials are available."""
    cfg = config_manager.load_config()
    cfg.cloud_mode = False
    cfg.cloud_api_key = None  # No API key
    cfg.set_project_mode("research", ProjectMode.CLOUD)
    config_manager.save_config(cfg)

    # No OAuth token file either → should raise
    with pytest.raises(
        RuntimeError,
        match="no credentials found",
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
async def test_get_client_local_project_honored_with_global_cloud_enabled(
    config_manager, config_home
):
    """LOCAL project mode should take priority over global cloud mode fallback."""
    cfg = config_manager.load_config()
    cfg.cloud_mode = True
    cfg.cloud_host = "https://cloud.example.test"
    cfg.cloud_api_key = None
    # "main" defaults to LOCAL since we didn't set_project_mode
    config_manager.save_config(cfg)

    # Should use ASGI transport without requiring OAuth token.
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


# --- Per-project cloud routing with force-local ---


@pytest.mark.asyncio
async def test_get_client_per_project_cloud_bypasses_force_local(
    config_manager, config_home, monkeypatch
):
    """CLOUD project routes to cloud even when BASIC_MEMORY_FORCE_LOCAL is set."""
    cfg = config_manager.load_config()
    cfg.cloud_mode = False
    cfg.cloud_host = "https://cloud.example.test"
    cfg.cloud_api_key = "bmc_test_key_123"
    cfg.set_project_mode("research", ProjectMode.CLOUD)
    config_manager.save_config(cfg)

    monkeypatch.setenv("BASIC_MEMORY_FORCE_LOCAL", "true")

    async with get_client(project_name="research") as client:
        assert str(client.base_url).rstrip("/") == "https://cloud.example.test/proxy"
        assert client.headers.get("Authorization") == "Bearer bmc_test_key_123"


@pytest.mark.asyncio
async def test_get_client_local_project_respects_force_local(
    config_manager, config_home, monkeypatch
):
    """LOCAL project still uses ASGI transport when BASIC_MEMORY_FORCE_LOCAL is set."""
    cfg = config_manager.load_config()
    cfg.cloud_mode = False
    cfg.cloud_api_key = "bmc_test_key_123"
    # "main" defaults to LOCAL
    config_manager.save_config(cfg)

    monkeypatch.setenv("BASIC_MEMORY_FORCE_LOCAL", "true")

    async with get_client(project_name="main") as client:
        assert isinstance(client._transport, httpx.ASGITransport)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_get_client_per_project_cloud_oauth_fallback(config_manager, config_home):
    """CLOUD project uses OAuth token when no API key is configured."""
    cfg = config_manager.load_config()
    cfg.cloud_mode = False
    cfg.cloud_host = "https://cloud.example.test"
    cfg.cloud_api_key = None  # No API key
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


# --- Explicit routing override tests ---


@pytest.mark.asyncio
async def test_get_client_explicit_routing_overrides_cloud_project(
    config_manager, config_home, monkeypatch
):
    """EXPLICIT_ROUTING + FORCE_LOCAL should override a CLOUD project to use local ASGI."""
    cfg = config_manager.load_config()
    cfg.cloud_mode = False
    cfg.cloud_host = "https://cloud.example.test"
    cfg.cloud_api_key = "bmc_test_key_123"
    cfg.set_project_mode("research", ProjectMode.CLOUD)
    config_manager.save_config(cfg)

    # Simulate CLI --local flag: sets both FORCE_LOCAL and EXPLICIT_ROUTING
    monkeypatch.setenv("BASIC_MEMORY_FORCE_LOCAL", "true")
    monkeypatch.setenv("BASIC_MEMORY_EXPLICIT_ROUTING", "true")

    async with get_client(project_name="research") as client:
        # Should use local ASGI transport, NOT cloud proxy
        assert isinstance(client._transport, httpx.ASGITransport)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_get_client_explicit_routing_cloud_flag_overrides_local_project(
    config_manager, config_home, monkeypatch
):
    """EXPLICIT_ROUTING + cloud mode should override a LOCAL project to use cloud."""
    cfg = config_manager.load_config()
    cfg.cloud_mode = True
    cfg.cloud_host = "https://cloud.example.test"
    cfg.cloud_client_id = "cid"
    cfg.cloud_domain = "https://auth.example.test"
    # "main" defaults to LOCAL
    config_manager.save_config(cfg)

    # Write OAuth token for cloud auth
    auth = CLIAuth(client_id=cfg.cloud_client_id, authkit_domain=cfg.cloud_domain)
    auth.token_file.parent.mkdir(parents=True, exist_ok=True)
    auth.token_file.write_text(
        '{"access_token":"token-cloud","refresh_token":null,"expires_at":9999999999,"token_type":"Bearer"}',
        encoding="utf-8",
    )

    # Simulate CLI --cloud flag: sets EXPLICIT_ROUTING, no FORCE_LOCAL
    monkeypatch.delenv("BASIC_MEMORY_FORCE_LOCAL", raising=False)
    monkeypatch.setenv("BASIC_MEMORY_EXPLICIT_ROUTING", "true")

    async with get_client(project_name="main") as client:
        # Should use cloud proxy, NOT local ASGI
        assert str(client.base_url).rstrip("/") == "https://cloud.example.test/proxy"
        assert client.headers.get("Authorization") == "Bearer token-cloud"
