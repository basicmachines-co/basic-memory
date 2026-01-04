"""CLI composition root for Basic Memory.

This container owns reading ConfigManager and environment variables for the
CLI entrypoint. Downstream modules receive config/dependencies explicitly
rather than reading globals.

Design principles:
- Only this module reads ConfigManager directly
- Runtime mode (cloud/local/test) is resolved here
- Different CLI commands may need different initialization
"""

from dataclasses import dataclass

from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.runtime import RuntimeMode, resolve_runtime_mode


@dataclass
class CliContainer:
    """Composition root for the CLI entrypoint.

    Holds resolved configuration and runtime context.
    Created once at CLI startup, then used by subcommands.
    """

    config: BasicMemoryConfig
    mode: RuntimeMode

    @classmethod
    def create(cls) -> "CliContainer":
        """Create container by reading ConfigManager.

        This is the single point where CLI reads global config.
        """
        config = ConfigManager().config
        mode = resolve_runtime_mode(
            cloud_mode_enabled=config.cloud_mode_enabled,
            is_test_env=config.is_test_env,
        )
        return cls(config=config, mode=mode)

    # --- Runtime Mode Properties ---

    @property
    def is_cloud_mode(self) -> bool:
        """Whether running in cloud mode."""
        return self.mode.is_cloud


# Module-level container instance (set by app callback)
_container: CliContainer | None = None


def get_container() -> CliContainer:
    """Get the current CLI container.

    Returns:
        The CLI container

    Raises:
        RuntimeError: If container hasn't been initialized
    """
    if _container is None:
        raise RuntimeError("CLI container not initialized. Call set_container() first.")
    return _container


def set_container(container: CliContainer) -> None:
    """Set the CLI container (called by app callback)."""
    global _container
    _container = container


def get_or_create_container() -> CliContainer:
    """Get existing container or create new one.

    This is useful for CLI commands that might be called before
    the main app callback runs (e.g., eager options).
    """
    global _container
    if _container is None:
        _container = CliContainer.create()
    return _container
