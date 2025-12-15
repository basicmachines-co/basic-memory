"""MCP server command with streamable HTTP transport."""

import asyncio
import os
import typer
from typing import Optional

from basic_memory.cli.app import app
from basic_memory.config import ConfigManager, init_mcp_logging

# Import mcp instance
from basic_memory.mcp.server import mcp as mcp_server  # pragma: no cover

# Import mcp tools to register them
import basic_memory.mcp.tools  # noqa: F401  # pragma: no cover

# Import prompts to register them
import basic_memory.mcp.prompts  # noqa: F401  # pragma: no cover
from loguru import logger
import threading
from basic_memory.services.initialization import initialize_file_sync

config = ConfigManager().config

if not config.cloud_mode_enabled:

    @app.command()
    def mcp(
        transport: str = typer.Option(
            "stdio", help="Transport type: stdio, streamable-http, or sse"
        ),
        host: str = typer.Option(
            "0.0.0.0", help="Host for HTTP transports (use 0.0.0.0 to allow external connections)"
        ),
        port: int = typer.Option(8000, help="Port for HTTP transports"),
        path: str = typer.Option("/mcp", help="Path prefix for streamable-http transport"),
        project: Optional[str] = typer.Option(None, help="Restrict MCP server to single project"),
    ):  # pragma: no cover
        """Run the MCP server with configurable transport options.

        This command starts an MCP server using one of three transport options:

        - stdio: Standard I/O (good for local usage)
        - streamable-http: Recommended for web deployments (default)
        - sse: Server-Sent Events (for compatibility with existing clients)
        """
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

        app_config = ConfigManager().config

        def run_file_sync():
            """Run file sync in a separate thread with its own event loop."""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(initialize_file_sync(app_config))
            except Exception as e:
                logger.error(f"File sync error: {e}", err=True)
            finally:
                loop.close()

        logger.info(f"Sync changes enabled: {app_config.sync_changes}")
        if app_config.sync_changes:
            # Start the sync thread
            sync_thread = threading.Thread(target=run_file_sync, daemon=True)
            sync_thread.start()
            logger.info("Started file sync in background")

        # Now run the MCP server (blocks)
        logger.info(f"Starting MCP server with {transport.upper()} transport")

        if transport == "stdio":
            mcp_server.run(
                transport=transport,
            )
        elif transport == "streamable-http" or transport == "sse":
            mcp_server.run(
                transport=transport,
                host=host,
                port=port,
                path=path,
                log_level="INFO",
            )
