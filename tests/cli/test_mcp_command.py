"""Tests for the `basic-memory mcp` command's HTTP bind defaults (#1578).

The HTTP/SSE transports have no inbound authentication, so the command must bind to
loopback by default, warn when bound elsewhere, and install FastMCP's Host/Origin guard
on both transports while honoring FastMCP's configured allowlists.
"""

from typing import Any, Literal

import fastmcp
import httpx
import pytest
from fastmcp.server.http import HostOriginGuardMiddleware
from loguru import logger
from typer.testing import CliRunner

import basic_memory.cli.commands.mcp as mcp_mod
from basic_memory.cli.auto_update import AutoUpdateResult, AutoUpdateStatus, InstallSource
from basic_memory.cli.main import app as cli_app
from basic_memory.mcp.server import mcp as live_mcp_server

runner = CliRunner()

type HttpTransport = Literal["streamable-http", "sse"]

WARNING_TEXT = "has no authentication"


@pytest.fixture
def captured_run(monkeypatch) -> dict[str, Any]:
    """Replace the server run with a recorder so the command returns immediately."""
    captured: dict[str, Any] = {}

    def record_run(*args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(mcp_mod.mcp_server, "run", record_run)
    monkeypatch.setattr(mcp_mod, "init_mcp_logging", lambda: None)
    return captured


def _guard_middleware_count(middleware: list[Any]) -> int:
    return sum(1 for entry in middleware if entry.cls is HostOriginGuardMiddleware)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("127.0.0.2", True),
        ("localhost", True),
        ("LOCALHOST", True),
        ("::1", True),
        ("[::1]", True),
        ("0.0.0.0", False),
        ("::", False),
        ("192.168.1.10", False),
        ("my-laptop.local", False),
    ],
)
def test_is_loopback_host(host: str, expected: bool) -> None:
    assert mcp_mod.is_loopback_host(host) is expected


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
def test_http_transports_delegate_guard_to_fastmcp(
    transport: HttpTransport, captured_run: dict[str, Any]
) -> None:
    result = runner.invoke(cli_app, ["mcp", "--transport", transport])

    assert result.exit_code == 0, result.output
    assert captured_run["host"] == "127.0.0.1"
    assert captured_run["host_origin_protection"] == "auto"
    # FastMCP builds the guard itself; our own middleware would guard twice.
    assert "middleware" not in captured_run
    assert WARNING_TEXT not in result.output


def test_non_loopback_host_warns(captured_run: dict[str, Any]) -> None:
    result = runner.invoke(cli_app, ["mcp", "--transport", "sse", "--host", "0.0.0.0"])

    assert result.exit_code == 0, result.output
    assert captured_run["host"] == "0.0.0.0"
    assert captured_run["host_origin_protection"] == "auto"
    assert WARNING_TEXT in result.output
    assert "0.0.0.0" in result.output


# --- Real ASGI path ---
# Build the app with the same options `mcp_server.run` receives and send requests
# through it. The guard's "auto" mode only validates connections whose local address
# is loopback; httpx's ASGITransport sets scope["server"] from the request URL host,
# so a 127.0.0.1 base URL plus a loopback client reproduces a local connection.
# Unknown paths are used so a request that passes the guard gets the router's 404.


def _loopback_client(transport: HttpTransport) -> httpx.AsyncClient:
    app = live_mcp_server.http_app(transport=transport, host_origin_protection="auto")
    asgi = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    return httpx.AsyncClient(transport=asgi, base_url="http://127.0.0.1:8000")


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
@pytest.mark.asyncio
async def test_guard_rejects_foreign_host_and_origin_on_loopback(
    transport: HttpTransport,
) -> None:
    async with _loopback_client(transport) as client:
        foreign_host = await client.get("/not-a-route", headers={"Host": "evil.example.com"})
        foreign_origin = await client.get(
            "/not-a-route", headers={"Origin": "http://evil.example.com"}
        )
        loopback = await client.get("/not-a-route")

    assert foreign_host.status_code == 421
    assert foreign_origin.status_code == 403
    assert loopback.status_code == 404


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
@pytest.mark.asyncio
async def test_guard_honors_fastmcp_allowlists(
    transport: HttpTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A loopback deployment behind a local reverse proxy configures these through
    # FASTMCP_HTTP_ALLOWED_HOSTS / FASTMCP_HTTP_ALLOWED_ORIGINS.
    monkeypatch.setattr(fastmcp.settings, "http_allowed_hosts", ["mcp.example.test"])
    monkeypatch.setattr(fastmcp.settings, "http_allowed_origins", ["https://app.example.test"])

    async with _loopback_client(transport) as client:
        proxied_host = await client.get("/not-a-route", headers={"Host": "mcp.example.test"})
        allowed_origin = await client.get(
            "/not-a-route", headers={"Origin": "https://app.example.test"}
        )
        foreign_host = await client.get("/not-a-route", headers={"Host": "evil.example.com"})

    assert proxied_host.status_code == 404
    assert allowed_origin.status_code == 404
    assert foreign_host.status_code == 421


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
def test_guard_installed_once_per_transport(transport: HttpTransport) -> None:
    app = live_mcp_server.http_app(transport=transport, host_origin_protection="auto")

    assert _guard_middleware_count(app.user_middleware) == 1


# --- Background update at MCP startup (#2000) ---


def _logged_messages(result: AutoUpdateResult) -> list[str]:
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(message.record["message"]))
    try:
        mcp_mod.log_background_update(result)
    finally:
        logger.remove(sink_id)
    return messages


def test_background_update_logs_a_major_release_announcement():
    announcement = "Basic Memory 2.0.0 is a new major version, so it was not installed."
    result = AutoUpdateResult(
        status=AutoUpdateStatus.UPDATE_AVAILABLE,
        source=InstallSource.UV_TOOL,
        checked=True,
        update_available=True,
        updated=False,
        latest_version="2.0.0",
        message=announcement,
    )

    assert _logged_messages(result) == [announcement]


def test_background_update_logs_install_and_failure_and_skips_up_to_date():
    installed = AutoUpdateResult(
        status=AutoUpdateStatus.UPDATED,
        source=InstallSource.UV_TOOL,
        checked=True,
        update_available=True,
        updated=True,
        restart_recommended=True,
    )
    current = AutoUpdateResult(
        status=AutoUpdateStatus.UP_TO_DATE,
        source=InstallSource.UV_TOOL,
        checked=True,
        update_available=False,
        updated=False,
        message="Basic Memory is up to date (1.0.1).",
    )

    failed = AutoUpdateResult(
        status=AutoUpdateStatus.FAILED,
        source=InstallSource.UV_TOOL,
        checked=True,
        update_available=True,
        updated=False,
        error="uv exited 2",
    )

    assert _logged_messages(installed) == [
        "A newer Basic Memory version was installed and will apply on next restart."
    ]
    assert _logged_messages(failed) == ["MCP background auto-update failed: uv exited 2"]
    assert _logged_messages(current) == []
