import os
from httpx import ASGITransport, AsyncClient
from loguru import logger

from basic_memory.api.app import app as fastapi_app


def create_client() -> AsyncClient:
    """Create an HTTP client based on configuration.

    Returns:
        AsyncClient configured for either local ASGI or remote HTTP transport
    """
    BASIC_MEMORY_USE_REMOTE_API = os.getenv("BASIC_MEMORY_USE_REMOTE_API", None)
    logger.info(f"BASIC_MEMORY_USE_REMOTE_API: {BASIC_MEMORY_USE_REMOTE_API}")

    if BASIC_MEMORY_USE_REMOTE_API:
        # Use HTTP transport for remote API with dynamic base URL
        logger.debug("Creating HTTP client for remote Basic Memory API (dynamic base URL)")
        return AsyncClient()  # No base_url - will be determined from headers at runtime
    else:
        # Use ASGI transport for local API
        logger.debug("Creating ASGI client for local Basic Memory API")
        return AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test")


# Create shared async client
client = create_client()
