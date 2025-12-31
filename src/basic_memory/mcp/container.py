"""Composition root for MCP entrypoint.

This module owns:
- Reading ConfigManager + environment variables
- Selecting runtime mode (cloud/local/test)
- Providing factories (httpx clients, repositories, services)
- Initializing logging for MCP

This centralizes composition concerns and reduces coupling between
modules and runtime environment decisions.
"""

from contextlib import asynccontextmanager, AbstractAsyncContextManager
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Optional

from httpx import ASGITransport, AsyncClient, Timeout
from loguru import logger

from basic_memory.config import BasicMemoryConfig, ConfigManager, init_mcp_logging


@dataclass
class MCPContainer:
    """Composition root for MCP entrypoint.

    Responsibilities:
    - Configuration loading and caching
    - Logging initialization
    - Runtime mode determination
    - HTTP client factory provision

    The container is built once at startup and provides factories
    for creating properly configured dependencies.
    """

    config: BasicMemoryConfig

    @classmethod
    def create(cls) -> "MCPContainer":
        """Build container with all dependencies.

        This is the single point where we:
        1. Initialize logging for MCP mode (file only, no stdout)
        2. Load configuration from file + environment
        3. Determine runtime mode (cloud/local/test)

        Returns:
            Configured MCPContainer ready for use
        """
        # Initialize logging first (MCP: file only, never stdout)
        init_mcp_logging()

        # Load configuration (merges file + environment variables)
        config = ConfigManager().config

        return cls(config=config)

    @property
    def is_cloud_mode(self) -> bool:
        """Check if running in cloud mode.

        Returns:
            True if cloud mode enabled via env var or config file
        """
        return self.config.cloud_mode_enabled

    @property
    def is_test_env(self) -> bool:
        """Check if running in test environment.

        Returns:
            True if test environment detected
        """
        return self.config.is_test_env

    def get_client_factory(
        self, override_factory: Optional[Callable[[], AbstractAsyncContextManager[AsyncClient]]] = None
    ) -> Callable[[], AbstractAsyncContextManager[AsyncClient]]:
        """Get HTTP client factory based on runtime mode.

        Priority order:
        1. Override factory (for dependency injection in tests/cloud)
        2. Cloud mode: HTTP client with auth
        3. Local mode: ASGI transport for in-process calls

        Args:
            override_factory: Optional factory override for testing/cloud

        Returns:
            Async context manager factory that yields configured AsyncClient
        """
        if override_factory:
            return override_factory

        # Return a factory that creates clients based on current mode
        @asynccontextmanager
        async def _client_factory() -> AsyncIterator[AsyncClient]:
            """Factory that creates client based on runtime mode."""
            timeout = Timeout(
                connect=10.0,  # 10 seconds for connection
                read=30.0,  # 30 seconds for reading response
                write=30.0,  # 30 seconds for writing request
                pool=30.0,  # 30 seconds for connection pool
            )

            if self.is_cloud_mode:
                # Cloud mode: inject auth when creating client
                from basic_memory.cli.auth import CLIAuth

                auth = CLIAuth(
                    client_id=self.config.cloud_client_id, authkit_domain=self.config.cloud_domain
                )
                token = await auth.get_valid_token()

                if not token:
                    raise RuntimeError(
                        "Cloud mode enabled but not authenticated. "
                        "Run 'basic-memory cloud login' first."
                    )

                # Auth header set ONCE at client creation
                proxy_base_url = f"{self.config.cloud_host}/proxy"
                logger.info(f"Creating HTTP client for cloud proxy at: {proxy_base_url}")
                async with AsyncClient(
                    base_url=proxy_base_url,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=timeout,
                ) as client:
                    yield client
            else:
                # Local mode: ASGI transport for in-process calls
                from basic_memory.api.app import app as fastapi_app

                logger.info("Creating ASGI client for local Basic Memory API")
                async with AsyncClient(
                    transport=ASGITransport(app=fastapi_app), base_url="http://test", timeout=timeout
                ) as client:
                    yield client

        return _client_factory
