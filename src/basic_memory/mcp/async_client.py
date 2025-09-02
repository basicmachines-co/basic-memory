from httpx import ASGITransport, AsyncClient
from loguru import logger

from basic_memory.api.app import app as fastapi_app
from basic_memory.config import ConfigManager


def create_client() -> AsyncClient:
    """Create an HTTP client based on configuration.

    Returns:
        AsyncClient configured for either local ASGI or remote HTTP transport
    """
    config_manager = ConfigManager()
    config = config_manager.load_config()

    if config.api_url:
        # Use HTTP transport for remote API
        logger.info(f"Creating HTTP client for remote Basic Memory API: {config.api_url}")
        return AsyncClient(base_url=config.api_url)
    else:
        # Use ASGI transport for local API
        logger.debug("Creating ASGI client for local Basic Memory API")
        return AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test")


class LazyClient:
    """A lazy-loading wrapper for the async client."""
    
    def __init__(self):
        self._client: AsyncClient | None = None
    
    def _ensure_client(self) -> AsyncClient:
        """Ensure the client is created."""
        if self._client is None:
            self._client = create_client()
        return self._client
    
    def __getattr__(self, name):
        """Delegate all attribute access to the underlying client."""
        return getattr(self._ensure_client(), name)
    
    def __call__(self, *args, **kwargs):
        """Make the lazy client callable if needed."""
        return self._ensure_client()(*args, **kwargs)


# Create shared lazy client that won't hang during imports
client = LazyClient()
