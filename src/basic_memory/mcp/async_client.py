import os
from httpx import ASGITransport, AsyncClient
from loguru import logger

from basic_memory.api.app import app as fastapi_app


def create_client() -> AsyncClient:
    """Create an HTTP client based on configuration.

    Returns:
        AsyncClient configured for either local ASGI or remote proxy
    """
    proxy_base_url = os.getenv("BASIC_MEMORY_PROXY_URL", None)
    logger.info(f"BASIC_MEMORY_PROXY_URL: {proxy_base_url}")

    if proxy_base_url:
        # Use HTTP transport to proxy endpoint
        logger.info(f"Creating HTTP client for proxy at: {proxy_base_url}")
        return AsyncClient(base_url=proxy_base_url)
    else:
        # Default: use ASGI transport for local API (development mode)
        logger.debug("Creating ASGI client for local Basic Memory API")
        return AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test")


# Create shared async client
client = create_client()
