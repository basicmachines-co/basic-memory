"""
Basic Memory FastMCP server.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Optional, Any

from fastmcp import FastMCP

from basic_memory.config import ConfigManager
from basic_memory.services.initialization import initialize_app


@dataclass
class AppContext:
    watch_task: Optional[asyncio.Task]
    migration_manager: Optional[Any] = None


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:  # pragma: no cover
    """ """
    # defer import so tests can monkeypatch
    from basic_memory.mcp.project_session import session

    app_config = ConfigManager().config
    # Initialize on startup (now returns migration_manager)
    migration_manager = await initialize_app(app_config)

    # Initialize project session with default project
    session.initialize(app_config.default_project)

    try:
        yield AppContext(watch_task=None, migration_manager=migration_manager)
    finally:
        # Cleanup on shutdown - migration tasks will be cancelled automatically
        pass


# Create the shared server instance with custom Stytch auth
mcp = FastMCP(
    name="Basic Memory",
    lifespan=app_lifespan,
)
