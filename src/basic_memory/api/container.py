"""API composition root for Basic Memory.

This container owns reading ConfigManager and environment variables for the
API entrypoint. Downstream modules receive config/dependencies explicitly
rather than reading globals.

Design principles:
- Only this module reads ConfigManager directly
- Runtime mode (cloud/local/test) is resolved here
- Factories for services are provided, not singletons
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, AsyncSession

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.runtime import RuntimeMode, resolve_runtime_mode

if TYPE_CHECKING:  # pragma: no cover
    from basic_memory.sync import SyncCoordinator


@dataclass
class ApiContainer:
    """Composition root for the API entrypoint.

    Holds resolved configuration and runtime context.
    Created once at app startup, then used to wire dependencies.
    """

    config: BasicMemoryConfig
    mode: RuntimeMode

    # --- Database ---
    # Cached database connections (set during lifespan startup)
    engine: AsyncEngine | None = None
    session_maker: async_sessionmaker[AsyncSession] | None = None

    @classmethod
    def create(cls) -> "ApiContainer":  # pragma: no cover
        """Create container by reading ConfigManager.

        This is the single point where API reads global config.
        """
        config = ConfigManager().config
        mode = resolve_runtime_mode(
            cloud_mode_enabled=config.cloud_mode_enabled,
            is_test_env=config.is_test_env,
        )
        return cls(config=config, mode=mode)

    # --- Runtime Mode Properties ---

    @property
    def should_sync_files(self) -> bool:
        """Whether file sync should be started.

        Sync is enabled when:
        - sync_changes is True in config
        - Not in test mode (tests manage their own sync)
        """
        return self.config.sync_changes and not self.mode.is_test

    @property
    def sync_skip_reason(self) -> str | None:  # pragma: no cover
        """Reason why sync is skipped, or None if sync should run.

        Useful for logging why sync was disabled.
        """
        if self.mode.is_test:
            return "Test environment detected"
        if not self.config.sync_changes:
            return "Sync changes disabled"
        return None

    def create_sync_coordinator(self) -> "SyncCoordinator":  # pragma: no cover
        """Create a SyncCoordinator with this container's settings.

        Returns:
            SyncCoordinator configured for this runtime environment
        """
        # Deferred import to avoid circular dependency
        from basic_memory.sync import SyncCoordinator

        return SyncCoordinator(
            config=self.config,
            should_sync=self.should_sync_files,
            skip_reason=self.sync_skip_reason,
        )

    # --- Database Factory ---

    async def init_database(  # pragma: no cover
        self,
    ) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
        """Initialize and cache database connections.

        Returns:
            Tuple of (engine, session_maker)
        """
        engine, session_maker = await db.get_or_create_db(self.config.database_path)
        self.engine = engine
        self.session_maker = session_maker
        return engine, session_maker

    async def shutdown_database(self) -> None:  # pragma: no cover
        """Clean up database connections."""
        await db.shutdown_db()


# Module-level container instance (set by lifespan)
# This allows deps.py to access the container without reading ConfigManager
_container: ApiContainer | None = None


def get_container() -> ApiContainer:
    """Get the current API container.

    Raises:
        RuntimeError: If container hasn't been initialized
    """
    if _container is None:
        raise RuntimeError("API container not initialized. Call set_container() first.")
    return _container


def set_container(container: ApiContainer) -> None:
    """Set the API container (called by lifespan)."""
    global _container
    _container = container
