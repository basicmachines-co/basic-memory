import os
from httpx import ASGITransport, AsyncClient, Timeout
from loguru import logger

from basic_memory.api.app import app as fastapi_app
from basic_memory.config import ConfigManager


def create_client() -> AsyncClient:
    """Create an HTTP client based on configuration.

    Priority for determining proxy URL:
    1. BASIC_MEMORY_PROXY_URL environment variable (highest priority)
    2. config.cloud_host if config.cloud_mode is True
    3. None (use local ASGI)

    Returns:
        AsyncClient configured for either local ASGI or remote proxy
    """
    # Check environment variable first
    proxy_base_url = os.getenv("BASIC_MEMORY_PROXY_URL", None)

    # If not in environment, check config
    if not proxy_base_url:
        try:
            config_manager = ConfigManager()
            config = config_manager.config
            if config.cloud_mode:
                proxy_base_url = config.cloud_host
                logger.info(f"Cloud mode enabled, using cloud_host: {proxy_base_url}")
        except Exception as e:
            # If config loading fails, fall back to local mode
            logger.warning(f"Failed to load config for cloud mode check: {e}")
            proxy_base_url = None

    logger.info(f"Proxy URL: {proxy_base_url}")

    # Configure timeout for longer operations like write_note
    # Default httpx timeout is 5 seconds which is too short for file operations
    timeout = Timeout(
        connect=10.0,  # 10 seconds for connection
        read=30.0,  # 30 seconds for reading response
        write=30.0,  # 30 seconds for writing request
        pool=30.0,  # 30 seconds for connection pool
    )

    if proxy_base_url:
        # Use HTTP transport to proxy endpoint
        logger.info(f"Creating HTTP client for proxy at: {proxy_base_url}")
        return AsyncClient(base_url=proxy_base_url, timeout=timeout)
    else:
        # Default: use ASGI transport for local API (development mode)
        logger.debug("Creating ASGI client for local Basic Memory API")
        return AsyncClient(
            transport=ASGITransport(app=fastapi_app), base_url="http://test", timeout=timeout
        )


# Create shared async client
client = create_client()
