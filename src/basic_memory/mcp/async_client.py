from httpx import ASGITransport, AsyncClient, Timeout
from loguru import logger

from basic_memory.api.app import app as fastapi_app
from basic_memory.config import ConfigManager


def create_client() -> AsyncClient:
    """Create an HTTP client based on configuration.

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


# Create shared async client
client = create_client()
