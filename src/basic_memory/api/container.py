"""Composition root for API entrypoint.

This module owns:
- Reading ConfigManager + environment variables
- Selecting runtime mode (cloud/local/test)
- Providing factories (httpx clients, repositories, services)
- Initializing logging for API

This centralizes composition concerns and reduces coupling between
modules and runtime environment decisions.
"""

from dataclasses import dataclass

from basic_memory.config import BasicMemoryConfig, ConfigManager, init_api_logging


@dataclass
class APIContainer:
    """Composition root for API entrypoint.

    Responsibilities:
    - Configuration loading and caching
    - Logging initialization
    - Runtime mode determination

    The container is built once at startup and provides access to
    configuration throughout the API lifecycle.
    """

    config: BasicMemoryConfig

    @classmethod
    def create(cls) -> "APIContainer":
        """Build container with all dependencies.

        This is the single point where we:
        1. Initialize logging for API mode
        2. Load configuration from file + environment
        3. Determine runtime mode (cloud/local/test)

        Returns:
            Configured APIContainer ready for use
        """
        # Initialize logging first (cloud mode: stdout, local: file)
        init_api_logging()

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
