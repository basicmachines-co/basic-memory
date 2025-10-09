from contextlib import asynccontextmanager, AbstractAsyncContextManager
from typing import AsyncIterator, Callable, Optional

from httpx import ASGITransport, AsyncClient, Timeout
from loguru import logger

from basic_memory.api.app import app as fastapi_app
from basic_memory.config import ConfigManager


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
async def get_client() -> AsyncIterator[AsyncClient]:
    """Get an AsyncClient as a context manager.

    This function provides proper resource management for HTTP clients,
    ensuring connections are closed after use. It supports three modes:

    1. **Factory injection** (cloud app, tests):
       If a custom factory is set via set_client_factory(), use that.

    2. **CLI cloud mode**:
       When cloud_mode_enabled is True, create HTTP client with auth
       token from CLIAuth for requests to cloud proxy endpoint.

    3. **Local mode** (default):
       Use ASGI transport for in-process requests to local FastAPI app.

    Usage:
        async with get_client() as client:
            response = await client.get("/path")

    Yields:
        AsyncClient: Configured HTTP client for the current mode

    Raises:
        RuntimeError: If cloud mode is enabled but user is not authenticated
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

        if config.cloud_mode_enabled:
            # CLI cloud mode: inject auth when creating client
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

    if config.cloud_mode_enabled:
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
