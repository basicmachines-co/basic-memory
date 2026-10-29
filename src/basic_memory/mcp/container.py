"""MCP composition root for Basic Memory.

This container owns reading ConfigManager and environment variables for the
MCP server entrypoint. Downstream modules receive config/dependencies explicitly
rather than reading globals.

Design principles:
- Only this module reads ConfigManager directly
- Runtime mode (cloud/local/test) is resolved here
- File indexing decisions are centralized here
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.read_cache.redis import RedisReadCache
from basic_memory.runtime.mode import RuntimeMode, resolve_runtime_mode

if TYPE_CHECKING:  # pragma: no cover
    from basic_memory.index.watch_coordinator import WatchCoordinator


@dataclass
class McpContainer:
    """Composition root for the MCP server entrypoint.

    Holds resolved configuration and runtime context.
    Created once at server startup, then used to wire dependencies.
    """

    config: BasicMemoryConfig
    mode: RuntimeMode
    read_cache: RedisReadCache | None = None

    @classmethod
    def create(cls) -> "McpContainer":
        """Create container by reading ConfigManager.

        This is the single point where MCP reads global config.
        """
        config = ConfigManager().config
        mode = resolve_runtime_mode(
            is_test_env=config.is_test_env,
        )
        return cls(config=config, mode=mode)

    # --- Runtime Mode Properties ---

    @property
    def should_watch_files(self) -> bool:
        """Whether local file watching should be started.

        Watching is enabled when:
        - index_changes is True in config
        - Not in test mode (tests manage their own watcher lifecycle)
        - Not in cloud mode (cloud handles storage events differently)
        """
        return self.config.index_changes and not self.mode.is_test and not self.mode.is_cloud

    @property
    def watch_skip_reason(self) -> str | None:
        """Reason why local watching is skipped, or None if it should run.

        Useful for logging why local watching was disabled.
        """
        if self.mode.is_test:
            return "Test environment detected"
        if self.mode.is_cloud:
            return "Cloud mode enabled"
        if not self.config.index_changes:
            return "Local file watching disabled"
        return None

    def create_watch_coordinator(self) -> "WatchCoordinator":
        """Create a WatchCoordinator with this container's settings.

        Returns:
            WatchCoordinator configured for this runtime environment
        """
        # Deferred import to avoid circular dependency
        from basic_memory.index.watch_coordinator import WatchCoordinator

        return WatchCoordinator(
            config=self.config,
            should_watch=self.should_watch_files,
            skip_reason=self.watch_skip_reason,
            read_cache=self.read_cache,
        )


# Container set for the whole process by an embedding host (set_container).
_container: McpContainer | None = None
# Containers of the lifespans running now, oldest first.
_lifespan_containers: list[McpContainer] = []


def get_container() -> McpContainer:
    """Get the current MCP container: the newest running lifespan's, else the host's.

    Raises:
        RuntimeError: If container hasn't been initialized
    """
    if _lifespan_containers:
        return _lifespan_containers[-1]
    if _container is None:
        raise RuntimeError("MCP container not initialized. Call set_container() first.")
    return _container


def set_container(container: McpContainer) -> None:
    """Set the process-wide MCP container for a host that runs no lifespan."""
    global _container
    _container = container


@contextmanager
def installed_container(container: McpContainer) -> Iterator[None]:
    """Expose a container for one server lifespan and withdraw it when that lifespan ends.

    Trigger: an in-process MCP session (``Client(mcp)``) enters and exits the lifespan.
    Why: a lifespan that set a global and never reset it left its config installed
      after the session ended, and later code in the same process, such as the
      default search mode, read a config that no longer applied (#1955). Saving and
      restoring a single slot is not enough either: two lifespans that overlap and
      exit out of order would restore an exited container.
    Outcome: each lifespan adds its container on entry and removes that same
      container, by identity, on exit; the newest still-running one is current.
    """
    _lifespan_containers.append(container)
    try:
        yield
    finally:
        newest_first = reversed(range(len(_lifespan_containers)))
        own_entry = next(i for i in newest_first if _lifespan_containers[i] is container)
        del _lifespan_containers[own_entry]
