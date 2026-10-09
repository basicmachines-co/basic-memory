"""Tests for the `basic-memory mcp` command's HTTP bind defaults (#1578).

The HTTP/SSE transports have no inbound authentication, so the command must bind to
loopback by default, warn when bound elsewhere, and enable FastMCP's Host/Origin guard.
"""

from typing import Any

import pytest
from typer.testing import CliRunner

import basic_memory.cli.commands.mcp as mcp_mod
from basic_memory.cli.main import app as cli_app

runner = CliRunner()

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
    assert captured_run["host_origin_protection"] == "auto"
    assert WARNING_TEXT not in result.output


def test_non_loopback_host_warns_and_keeps_origin_protection(
    captured_run: dict[str, Any],
) -> None:
    result = runner.invoke(cli_app, ["mcp", "--transport", "sse", "--host", "0.0.0.0"])

    assert result.exit_code == 0, result.output
    assert captured_run["host"] == "0.0.0.0"
    assert captured_run["host_origin_protection"] == "auto"
    assert WARNING_TEXT in result.output
    assert "0.0.0.0" in result.output
