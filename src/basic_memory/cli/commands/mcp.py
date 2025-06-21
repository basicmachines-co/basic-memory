"""MCP server command with streamable HTTP transport."""

import asyncio
from loguru import logger


from basic_memory.cli.app import app
from basic_memory.config import app_config

# Import mcp instance
from basic_memory.mcp.server import mcp as mcp_server  # pragma: no cover

# Import mcp tools to register them
import basic_memory.mcp.tools  # noqa: F401  # pragma: no cover

# Import prompts to register them
import basic_memory.mcp.prompts  # noqa: F401  # pragma: no cover


@app.command()
def mcp():  # pragma: no cover
    """
    Run the MCP server with stdio transport.
    """

    from basic_memory.services.initialization import initialize_file_sync

    # Start the MCP server with the specified transport

    # Use unified thread-based sync approach for both transports
    import threading

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
    logger.info("Starting MCP server with stdio transport")

    mcp_server.run(
        transport="stdio",
    )
