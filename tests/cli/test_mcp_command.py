"""Tests for the `basic-memory mcp` command's HTTP bind defaults (#1578).

The HTTP/SSE transports have no inbound authentication, so the command must bind to
loopback by default, warn when bound elsewhere, and install FastMCP's Host/Origin guard
on both transports.
"""

from typing import Any, Literal

import httpx
import pytest
from fastmcp.server.http import HostOriginGuardMiddleware
from typer.testing import CliRunner

import basic_memory.cli.commands.mcp as mcp_mod
from basic_memory.cli.main import app as cli_app
from basic_memory.mcp.server import mcp as live_mcp_server

runner = CliRunner()

WARNING_TEXT = "has no authentication"

type HttpTransport = Literal["streamable-http", "sse"]


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
def test_http_transport_defaults_to_loopback_with_origin_protection(
    transport: str, captured_run: dict[str, Any]
) -> None:
    result = runner.invoke(cli_app, ["mcp", "--transport", transport])

    assert result.exit_code == 0, result.output
    assert captured_run["host"] == "127.0.0.1"
    assert _guard_middleware_count(captured_run["middleware"]) == 1
    # FastMCP's own option stays off so streamable-http is not guarded twice.
    assert "host_origin_protection" not in captured_run
    assert WARNING_TEXT not in result.output


def test_non_loopback_host_warns_and_keeps_origin_protection(
    captured_run: dict[str, Any],
) -> None:
    result = runner.invoke(cli_app, ["mcp", "--transport", "sse", "--host", "0.0.0.0"])

    assert result.exit_code == 0, result.output
    assert captured_run["host"] == "0.0.0.0"
    assert _guard_middleware_count(captured_run["middleware"]) == 1
    assert WARNING_TEXT in result.output
    assert "0.0.0.0" in result.output


# --- Real ASGI path ---
# Build the app the way `mcp_server.run` does for each transport and send requests
# through it. The guard's "auto" mode only validates connections whose local address
# is loopback; httpx's ASGITransport sets scope["server"] from the request URL host,
# so a 127.0.0.1 base URL plus a loopback client reproduces a local connection.


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
@pytest.mark.asyncio
async def test_guard_rejects_foreign_host_and_origin_on_loopback(
    transport: HttpTransport,
) -> None:
    app = live_mcp_server.http_app(transport=transport, middleware=mcp_mod.http_guard_middleware())
    asgi = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))

    async with httpx.AsyncClient(transport=asgi, base_url="http://127.0.0.1:8000") as client:
        foreign_host = await client.get("/not-a-route", headers={"Host": "evil.example.com"})
        foreign_origin = await client.get(
            "/not-a-route", headers={"Origin": "http://evil.example.com"}
        )
        loopback = await client.get("/not-a-route")

    assert foreign_host.status_code == 421
    assert foreign_origin.status_code == 403
    # A 404 from the router proves the loopback request got past the guard.
    assert loopback.status_code == 404


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
def test_guard_installed_once_per_transport(transport: HttpTransport) -> None:
    app = live_mcp_server.http_app(transport=transport, middleware=mcp_mod.http_guard_middleware())

    assert _guard_middleware_count(app.user_middleware) == 1
