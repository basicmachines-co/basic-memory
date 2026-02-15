"""
Basic Memory FastMCP server.
"""

import time
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from loguru import logger

from basic_memory import db
from basic_memory.cli.auth import CLIAuth
from basic_memory.mcp.container import McpContainer, set_container
from basic_memory.services.initialization import initialize_app


@asynccontextmanager
async def lifespan(app: FastMCP):
    """Lifecycle manager for the MCP server.

    Handles:
    - Database initialization and migrations
    - File sync via SyncCoordinator (if enabled and not in cloud mode)
    - Proper cleanup on shutdown
    """
    # --- Composition Root ---
    # Create container and read config (single point of config access)
    container = McpContainer.create()
    set_container(container)

    config = container.config
    logger.info(f"Starting Basic Memory MCP server (mode={container.mode.name})")
    logger.info(
        f"Config: database_backend={config.database_backend.value}, "
        f"semantic_search_enabled={config.semantic_search_enabled}, "
        f"default_project={config.default_project}"
    )
    if config.semantic_search_enabled:
        logger.info(
            f"Semantic search: provider={config.semantic_embedding_provider}, "
            f"model={config.semantic_embedding_model}, "
            f"dimensions={config.semantic_embedding_dimensions or 'auto'}, "
            f"batch_size={config.semantic_embedding_batch_size}"
        )

    # Log configured projects with their routing mode
    for name, entry in config.projects.items():
        default = " (default)" if name == config.default_project else ""
        logger.info(f"Project: {name} -> {entry.path} [mode={entry.mode.value}]{default}")

    # Check cloud login status (local file check, no network call)
    if config.cloud_mode:
        auth = CLIAuth(client_id=config.cloud_client_id, authkit_domain=config.cloud_domain)
        tokens = auth.load_tokens()
        if tokens is None:
            logger.warning("Cloud mode enabled but not authenticated - run 'bm cloud login'")
        elif not auth.is_token_valid(tokens):
            expires_at = tokens.get("expires_at", 0)
            expired_ago = int(time.time() - expires_at)
            logger.warning(f"Cloud token expired {expired_ago}s ago - may need 'bm cloud login'")
        else:
            logger.info("Cloud: authenticated (token valid)")

        if config.cloud_api_key:
            logger.info("Cloud: API key configured (preferred for per-project routing)")
        else:
            logger.info("Cloud: no API key set (will use OAuth token for cloud projects)")

    # Track if we created the engine (vs test fixtures providing it)
    # This prevents disposing an engine provided by test fixtures when
    # multiple Client connections are made in the same test
    engine_was_none = db._engine is None

    # Initialize app (runs migrations, reconciles projects)
    await initialize_app(container.config)

    # Create and start sync coordinator (lifecycle centralized in coordinator)
    sync_coordinator = container.create_sync_coordinator()
    await sync_coordinator.start()

    try:
        yield
    finally:
        # Shutdown - coordinator handles clean task cancellation
        logger.debug("Shutting down Basic Memory MCP server")
        await sync_coordinator.stop()

        # Only shutdown DB if we created it (not if test fixture provided it)
        if engine_was_none:
            await db.shutdown_db()
            logger.debug("Database connections closed")
        else:  # pragma: no cover
            logger.debug("Skipping DB shutdown - engine provided externally")


mcp = FastMCP(
    name="Basic Memory",
    lifespan=lifespan,
)
