"""Runtime mode resolution for Basic Memory.

This module centralizes runtime mode detection, ensuring cloud/local/test
determination happens in one place rather than scattered across modules.

Composition roots (containers) read ConfigManager and use this module
to resolve the runtime mode, then pass the result downstream.
"""

from enum import Enum, auto


class RuntimeMode(Enum):
    """Runtime modes for Basic Memory."""

    LOCAL = auto()  # Local standalone mode (default)
    CLOUD = auto()  # Cloud mode with remote sync
    TEST = auto()  # Test environment

    @property
    def is_cloud(self) -> bool:
        return self == RuntimeMode.CLOUD

    @property
    def is_local(self) -> bool:
        return self == RuntimeMode.LOCAL

    @property
    def is_test(self) -> bool:
        return self == RuntimeMode.TEST


def resolve_runtime_mode(
    cloud_mode_enabled: bool,
    is_test_env: bool,
) -> RuntimeMode:
    """Resolve the runtime mode from configuration flags.

    This is the single source of truth for mode resolution.
    Composition roots call this with config values they've read.

    Args:
        cloud_mode_enabled: Whether cloud mode is enabled in config
        is_test_env: Whether running in test environment

    Returns:
        The resolved RuntimeMode
    """
    # Trigger: test environment is detected
    # Why: tests need special handling (no file sync, isolated DB)
    # Outcome: returns TEST mode, skipping cloud mode check
    if is_test_env:
        return RuntimeMode.TEST

    # Trigger: cloud mode is enabled in config
    # Why: cloud mode changes auth, sync, and API behavior
    # Outcome: returns CLOUD mode for remote-first behavior
    if cloud_mode_enabled:
        return RuntimeMode.CLOUD

    return RuntimeMode.LOCAL
