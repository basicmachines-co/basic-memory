"""Every HTTP client Basic Memory opens toward Cloud identifies itself.

httpx's default (`python-httpx/x`) looks like any script. These assert the header on
the wire, through a MockTransport swapped into the real clients the CLI and MCP open.
"""

import httpx
import pytest

from basic_memory import __version__
from basic_memory.cli.auth import CLIAuth
from basic_memory.cli.commands.cloud.api_client import _default_http_client
from basic_memory.config import ProjectMode
from basic_memory.mcp import async_client as async_client_module
from basic_memory.mcp.async_client import get_client, get_cloud_control_plane_client
from basic_memory.user_agent import BASIC_MEMORY_USER_AGENT


@pytest.fixture(autouse=True)
def _no_client_factory():
    async_client_module._client_factory = None
    yield
    async_client_module._client_factory = None


@pytest.fixture
def cloud_config(config_manager):
    cfg = config_manager.load_config()
    cfg.cloud_host = "https://cloud.example.test"
    cfg.cloud_api_key = "bmc_test_key_123"
    cfg.set_project_mode("research", ProjectMode.CLOUD)
    config_manager.save_config(cfg)
    return cfg


async def _sent_user_agent(client: httpx.AsyncClient, **request_kwargs) -> str:
    """Send one request through the client's own headers and return its User-Agent."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["user_agent"] = request.headers["User-Agent"]
        return httpx.Response(200, json={})

    client._transport = httpx.MockTransport(handler)  # pyright: ignore[reportPrivateUsage]
    await client.get("https://cloud.example.test/health", **request_kwargs)
    return seen["user_agent"]


def test_user_agent_names_the_package_version():
    assert BASIC_MEMORY_USER_AGENT == f"basic-memory/{__version__} (cli)"


@pytest.mark.asyncio
async def test_cloud_proxy_client_sends_the_user_agent(cloud_config):
    async with get_client(project_name="research") as client:
        assert await _sent_user_agent(client) == BASIC_MEMORY_USER_AGENT


@pytest.mark.asyncio
async def test_control_plane_client_sends_the_user_agent(cloud_config):
    async with get_cloud_control_plane_client(workspace="tenant-123") as client:
        assert await _sent_user_agent(client) == BASIC_MEMORY_USER_AGENT


@pytest.mark.asyncio
async def test_a_callers_user_agent_still_wins(cloud_config):
    async with get_cloud_control_plane_client() as client:
        sent = await _sent_user_agent(client, headers={"User-Agent": "custom/1.0"})
    assert sent == "custom/1.0"


@pytest.mark.asyncio
async def test_cloud_api_request_client_sends_the_user_agent():
    async with _default_http_client(timeout=5.0) as client:
        assert await _sent_user_agent(client) == BASIC_MEMORY_USER_AGENT


@pytest.mark.asyncio
async def test_cli_auth_client_sends_the_user_agent(config_manager):
    auth = CLIAuth(client_id="cid", authkit_domain="https://auth.example.test")
    async with auth._get_http_client() as client:  # pyright: ignore[reportPrivateUsage]
        assert await _sent_user_agent(client) == BASIC_MEMORY_USER_AGENT
