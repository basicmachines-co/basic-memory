"""Enhanced FastMCP server instance for Basic Memory."""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.logging import configure_logging as mcp_configure_logging
from dataclasses import dataclass

from basic_memory.config import config as project_config
from basic_memory.services.initialization import initialize_app

# mcp console logging
mcp_configure_logging(level="ERROR")


@dataclass
class AppContext:
    watch_task: Optional[asyncio.Task]


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:  # pragma: no cover
    """Manage application lifecycle with type-safe context"""
    # Initialize on startup
    watch_task = await initialize_app(project_config)
    try:
        yield AppContext(watch_task=watch_task)
    finally:
        # Cleanup on shutdown
        if watch_task:
            watch_task.cancel()


# Create the shared server instance
mcp = FastMCP("Basic Memory", log_level="ERROR", lifespan=app_lifespan)
