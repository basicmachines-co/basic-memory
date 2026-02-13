import os
from contextlib import asynccontextmanager, AbstractAsyncContextManager
from typing import AsyncIterator, Callable, Optional

from httpx import ASGITransport, AsyncClient, Timeout
from loguru import logger

from basic_memory.api.app import app as fastapi_app
from basic_memory.config import ConfigManager, ProjectMode


def _force_local_mode() -> bool:
    """Check if local mode is forced via environment variable.

    This allows commands like `bm mcp` to force local routing even when
    cloud_mode_enabled is True in config. The local MCP server should
    always talk to the local API, not the cloud proxy.

    Returns:
        True if BASIC_MEMORY_FORCE_LOCAL is set to a truthy value
    """
    return os.environ.get("BASIC_MEMORY_FORCE_LOCAL", "").lower() in ("true", "1", "yes")


# Optional factory override for dependency injection
_client_factory: Optional[Callable[[], AbstractAsyncContextManager[AsyncClient]]] = None


def set_client_factory(factory: Callable[[], AbstractAsyncContextManager[AsyncClient]]) -> None:
    """Override the default client factory (for cloud app, testing, etc).

    Args:
        factory: An async context manager that yields an AsyncClient

    Example:
        @asynccontextmanager
        async def custom_client_factory():
            async with AsyncClient(...) as client:
                yield client

        set_client_factory(custom_client_factory)
    """
    global _client_factory
    _client_factory = factory


@asynccontextmanager
async def get_client(
    project_name: Optional[str] = None,
) -> AsyncIterator[AsyncClient]:
    """Get an AsyncClient as a context manager.

    This function provides proper resource management for HTTP clients,
    ensuring connections are closed after use. Routing priority:

    1. **Factory injection** (cloud app, tests):
       If a custom factory is set via set_client_factory(), use that.

    2. **Per-project cloud mode** (project_name provided):
       If the project's mode is CLOUD, routes to cloud using API key or
       OAuth token. Honored even when FORCE_LOCAL is set, because the user
       explicitly declared this project as cloud.

    3. **Per-project local mode** (project_name provided):
       If the project's mode is LOCAL (or unspecified, default LOCAL), route
       to local ASGI transport. This allows mixed local/cloud routing even when
       global cloud mode is enabled.

    4. **Force-local** (BASIC_MEMORY_FORCE_LOCAL env var):
       Routes to local ASGI transport, ignoring global cloud settings.

    5. **Global cloud mode** (deprecated fallback):
       When cloud_mode_enabled is True, uses OAuth JWT token.

    6. **Local mode** (default):
       Use ASGI transport for in-process requests to local FastAPI app.

    Args:
        project_name: Optional project name for per-project routing.
            If provided and the project's mode is CLOUD, routes to cloud
            using the API key or OAuth token.

    Usage:
        async with get_client() as client:
            response = await client.get("/path")

        # Per-project routing
        async with get_client(project_name="research") as client:
            response = await client.get("/path")

    Yields:
        AsyncClient: Configured HTTP client for the current mode

    Raises:
        RuntimeError: If cloud routing needed but no API key / not authenticated
    """
    if _client_factory:
        # Use injected factory (cloud app, tests)
        async with _client_factory() as client:
            yield client
    else:
        # Default: create based on config
        config = ConfigManager().config
        timeout = Timeout(
            connect=10.0,  # 10 seconds for connection
            read=30.0,  # 30 seconds for reading response
            write=30.0,  # 30 seconds for writing request
            pool=30.0,  # 30 seconds for connection pool
        )

        # Trigger: project has per-project cloud mode set
        # Why: per-project CLOUD is an explicit user declaration that should be
        #      honored even from the MCP server (which sets FORCE_LOCAL)
        # Outcome: HTTP client with API key or OAuth auth to cloud proxy
        if project_name and config.get_project_mode(project_name) == ProjectMode.CLOUD:
            # Try API key first (explicit, no network)
            token = config.cloud_api_key
            if not token:
                # Fall back to OAuth session (may refresh token)
                from basic_memory.cli.auth import CLIAuth

                auth = CLIAuth(client_id=config.cloud_client_id, authkit_domain=config.cloud_domain)
                token = await auth.get_valid_token()

            if not token:
                raise RuntimeError(
                    f"Project '{project_name}' is set to cloud mode but no credentials found. "
                    "Run 'bm cloud set-key <key>' or 'bm cloud login' first."
                )

            proxy_base_url = f"{config.cloud_host}/proxy"
            logger.info(
                f"Creating HTTP client for cloud project '{project_name}' at: {proxy_base_url}"
            )
            async with AsyncClient(
                base_url=proxy_base_url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout,
            ) as client:
                yield client

        # Trigger: project is not explicitly cloud (LOCAL is the default)
        # Why: project-scoped routing should honor local mode even when global
        #      cloud mode is enabled for backward compatibility
        # Outcome: uses ASGI transport for in-process local API calls
        elif project_name and config.get_project_mode(project_name) == ProjectMode.LOCAL:
            logger.info(f"Project '{project_name}' is set to local mode - using ASGI transport")
            async with AsyncClient(
                transport=ASGITransport(app=fastapi_app), base_url="http://test", timeout=timeout
            ) as client:
                yield client

        # Trigger: BASIC_MEMORY_FORCE_LOCAL env var is set
        # Why: allows local MCP server and CLI commands to route locally
        #      even when cloud_mode_enabled is True
        # Outcome: uses ASGI transport for in-process local API calls
        elif _force_local_mode():
            logger.info("Force local mode enabled - using ASGI client for local Basic Memory API")
            async with AsyncClient(
                transport=ASGITransport(app=fastapi_app), base_url="http://test", timeout=timeout
            ) as client:
                yield client

        elif config.cloud_mode_enabled:
            # Global cloud mode (deprecated fallback): inject OAuth auth when creating client
            from basic_memory.cli.auth import CLIAuth

            auth = CLIAuth(client_id=config.cloud_client_id, authkit_domain=config.cloud_domain)
            token = await auth.get_valid_token()

            if not token:
                raise RuntimeError(
                    "Cloud mode enabled but not authenticated. "
                    "Run 'basic-memory cloud login' first."
                )

            # Auth header set ONCE at client creation
            proxy_base_url = f"{config.cloud_host}/proxy"
            logger.info(f"Creating HTTP client for cloud proxy at: {proxy_base_url}")
            async with AsyncClient(
                base_url=proxy_base_url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout,
            ) as client:
                yield client
        else:
            # Local mode: ASGI transport for in-process calls
            # Note: ASGI transport does NOT trigger FastAPI lifespan, so no special handling needed
            logger.info("Creating ASGI client for local Basic Memory API")
            async with AsyncClient(
                transport=ASGITransport(app=fastapi_app), base_url="http://test", timeout=timeout
            ) as client:
                yield client


def create_client() -> AsyncClient:
    """Create an HTTP client based on configuration.

    DEPRECATED: Use get_client() context manager instead for proper resource management.

    This function is kept for backward compatibility but will be removed in a future version.
    The returned client should be closed manually by calling await client.aclose().

    Returns:
        AsyncClient configured for either local ASGI or remote proxy
    """
    config_manager = ConfigManager()
    config = config_manager.config

    # Configure timeout for longer operations like write_note
    # Default httpx timeout is 5 seconds which is too short for file operations
    timeout = Timeout(
        connect=10.0,  # 10 seconds for connection
        read=30.0,  # 30 seconds for reading response
        write=30.0,  # 30 seconds for writing request
        pool=30.0,  # 30 seconds for connection pool
    )

    # Check force local first (for local MCP server and CLI --local flag)
    if _force_local_mode():
        logger.info("Force local mode enabled - using ASGI client for local Basic Memory API")
        return AsyncClient(
            transport=ASGITransport(app=fastapi_app), base_url="http://test", timeout=timeout
        )
    elif config.cloud_mode_enabled:
        # Use HTTP transport to proxy endpoint
        proxy_base_url = f"{config.cloud_host}/proxy"
        logger.info(f"Creating HTTP client for proxy at: {proxy_base_url}")
        return AsyncClient(base_url=proxy_base_url, timeout=timeout)
    else:
        # Default: use ASGI transport for local API (development mode)
        logger.info("Creating ASGI client for local Basic Memory API")
        return AsyncClient(
            transport=ASGITransport(app=fastapi_app), base_url="http://test", timeout=timeout
        )
