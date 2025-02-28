"""Main MCP entrypoint for Basic Memory.

Creates and configures the shared MCP instance and handles server startup.
"""

from loguru import logger  # pragma: no cover

from basic_memory.config import config  # pragma: no cover

# Import shared mcp instance
from basic_memory.mcp.server import mcp  # pragma: no cover

# Import tools to register them
import basic_memory.mcp.tools  # noqa: F401 # pragma: no cover

# Import prompts to register them
import basic_memory.mcp.prompts  # noqa: F401 # pragma: no cover



if __name__ == "__main__":  # pragma: no cover
    home_dir = config.home
    logger.info("Starting Basic Memory MCP server")
    logger.info(f"Home directory: {home_dir}")
    mcp.run()