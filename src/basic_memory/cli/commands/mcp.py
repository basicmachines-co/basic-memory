"""MCP server command with streamable HTTP transport."""

import ipaddress
import os
import threading
from typing import Any, Literal, Optional

import typer
from loguru import logger

from basic_memory.cli.app import app
from basic_memory.cli.auto_update import AutoUpdateStatus, run_auto_update
from basic_memory.config import ConfigManager, init_mcp_logging


class _DeferredMcpServer:
    def run(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        from basic_memory.mcp.server import mcp as live_mcp_server

        live_mcp_server.run(*args, **kwargs)


# Keep module-level attribute for tests/monkeypatching while deferring heavy import.
mcp_server = _DeferredMcpServer()


type HttpTransport = Literal["streamable-http", "sse"]


def http_guard_options(transport: HttpTransport) -> dict[str, Any]:
    """Return the `run` kwargs that install FastMCP's Host/Origin guard for a transport.

    In "auto" mode the guard checks Host and Origin only for connections arriving on
    a loopback address. That closes DNS rebinding from a browser tab and leaves
    0.0.0.0 container binds (Docker bridge) usable.
    """
    # Trigger: streamable-http transport.
    # Why: FastMCP builds its own guard there from host_origin_protection and the
    #      configured FASTMCP_HTTP_ALLOWED_HOSTS / FASTMCP_HTTP_ALLOWED_ORIGINS.
    # Outcome: delegate to FastMCP; adding our middleware too would guard twice.
    if transport == "streamable-http":
        return {"host_origin_protection": "auto"}

    # SSE: FastMCP 4.0.3's create_sse_app ignores host_origin_protection, so install
    # the same guard ourselves with the allowlists create_streamable_http_app would
    # read from fastmcp.settings.
    # Deferred: fastmcp/starlette are heavy and the CLI import path must stay light
    # (tests/cli/test_cli_exit.py guards this).
    import fastmcp
    from fastmcp.server.http import HostOriginGuardMiddleware
    from starlette.middleware import Middleware

    guard = Middleware(
        HostOriginGuardMiddleware,
        allowed_hosts=fastmcp.settings.http_allowed_hosts,
        allowed_origins=fastmcp.settings.http_allowed_origins,
        mode="auto",
    )
    return {"middleware": [guard]}


def is_loopback_host(host: str) -> bool:
    """Return True when an HTTP bind host only accepts connections from this machine."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        # Hostnames other than localhost can resolve to any interface, so treat
        # them as exposed rather than guessing what they resolve to.
        return False


@app.command()
def mcp(
    transport: str = typer.Option("stdio", help="Transport type: stdio, streamable-http, or sse"),
    host: str = typer.Option(
        "127.0.0.1",
        help=(
            "Host for HTTP transports. Defaults to loopback; use 0.0.0.0 to allow "
            "external connections (no authentication)"
        ),
    ),
    port: int = typer.Option(8000, help="Port for HTTP transports"),
    path: str = typer.Option("/mcp", help="Path prefix for streamable-http transport"),
    project: Optional[str] = typer.Option(None, help="Restrict MCP server to single project"),
):  # pragma: no cover
    """Run the MCP server with configurable transport options.

    This command starts an MCP server using one of three transport options:

    - stdio: Standard I/O (good for local usage)
    - streamable-http: Recommended for web deployments
    - sse: Server-Sent Events (for compatibility with existing clients)

    Initialization, file indexing, and cleanup are handled by the MCP server's lifespan.

    Note: This command is available regardless of cloud mode setting.
    Users who have cloud mode enabled can still use local MCP for Claude Code
    and Claude Desktop while using cloud MCP for web and mobile access.
    """
    # --- Routing setup ---
    # Trigger: MCP server command invocation.
    # Why: HTTP/SSE transports serve as local API endpoints and must never
    #      route through cloud. Stdio is a client-facing protocol that
    #      should honor per-project routing (local or cloud).
    # Outcome: HTTP/SSE get explicit local override; stdio passes through
    #          whatever env vars are already set (honoring external overrides)
    #          and defaults to per-project routing resolution.
    if transport in ("streamable-http", "sse"):
        os.environ["BASIC_MEMORY_FORCE_LOCAL"] = "true"
        os.environ.pop("BASIC_MEMORY_FORCE_CLOUD", None)
        os.environ["BASIC_MEMORY_EXPLICIT_ROUTING"] = "true"
    # stdio: no env var manipulation — per-project routing applies by default,
    # and externally-set env vars (e.g. BASIC_MEMORY_FORCE_CLOUD) are honored.

    # Import mcp tools/prompts to register them with the server
    import basic_memory.mcp.tools  # noqa: F401  # pragma: no cover
    import basic_memory.mcp.prompts  # noqa: F401  # pragma: no cover
    import basic_memory.mcp.resources  # noqa: F401  # pragma: no cover

    # Initialize logging for MCP (file only, stdout breaks protocol)
    init_mcp_logging()

    # Validate and set project constraint if specified
    if project:
        config_manager = ConfigManager()
        project_name, _ = config_manager.get_project(project)
        if not project_name:
            typer.echo(f"No project found named: {project}", err=True)
            raise typer.Exit(1)

        # Set env var with validated project name
        os.environ["BASIC_MEMORY_MCP_PROJECT"] = project_name
        logger.info(f"MCP server constrained to project: {project_name}")

    def _run_background_auto_update() -> None:
        result = run_auto_update(force=False, check_only=False, silent=True)
        if result.restart_recommended:
            logger.info(
                "A newer Basic Memory version was installed and will apply on next restart."
            )
        elif result.status == AutoUpdateStatus.FAILED and result.error:
            logger.warning(f"MCP background auto-update failed: {result.error}")

    # Trigger: stdio transport corresponds to local user installs.
    # Why: server transports (HTTP/SSE) run in managed environments where
    # package-manager self-upgrades are inappropriate.
    # Outcome: background auto-update runs only for local stdio MCP sessions.
    if transport == "stdio":
        threading.Thread(target=_run_background_auto_update, daemon=True).start()

    # Trigger: MCP server startup on the Postgres backend, before the transport
    # creates its event loop.
    # Why: the watcher/lifespan path runs startup migrations + engine.dispose() on
    # asyncpg, which races stdlib asyncio teardown and crashes the container loop
    # (#831/#877). uvloop's C scheduler structurally avoids that race and must own
    # the loop policy before the loop is created. No-op for SQLite.
    # Outcome: `basic-memory mcp` on Postgres runs on uvloop. (The CLI callback also
    # installs it; this keeps the server startup seam explicit and self-contained.)
    from basic_memory.db import maybe_install_uvloop

    maybe_install_uvloop(ConfigManager().config)

    # Run the MCP server (blocks)
    # Lifespan handles: initialization, migrations, file indexing, cleanup
    logger.info(f"Starting MCP server with {transport.upper()} transport")

    if transport == "stdio":
        mcp_server.run(
            transport=transport,
        )
    elif transport == "streamable-http" or transport == "sse":
        # Trigger: HTTP/SSE bind host is not a loopback address.
        # Why: these transports have no inbound authentication, so every MCP tool
        #      (write_note, delete_note, create_memory_project, ...) is reachable by
        #      anyone who can reach the port.
        # Outcome: warn on stderr and in the MCP log; startup proceeds because
        #          containers legitimately bind 0.0.0.0 behind their own network.
        if not is_loopback_host(host):
            warning = (
                f"MCP {transport} transport is bound to {host}, which is reachable from "
                "other machines. This server has no authentication; bind to 127.0.0.1 "
                "unless the network is trusted."
            )
            typer.echo(f"Warning: {warning}", err=True)
            logger.warning(warning)

        mcp_server.run(
            transport=transport,
            host=host,
            port=port,
            path=path,
            log_level="INFO",
            **http_guard_options(transport),
        )
