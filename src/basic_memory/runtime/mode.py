"""Runtime mode resolution for Basic Memory.

This module centralizes runtime mode detection, ensuring cloud/local/test
determination happens in one place rather than scattered across modules.

Composition roots read ConfigManager and use this module to resolve the runtime
mode, then pass the result downstream.
"""

import os
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
    is_test_env: bool,
) -> RuntimeMode:
    """Resolve the runtime mode from configuration flags.

    This is the single source of truth for mode resolution.
    Composition roots call this with config values they've read.

    Args:
        is_test_env: Whether running in test environment

    Returns:
        The resolved RuntimeMode
    """
    if is_test_env:
        return RuntimeMode.TEST

    # Trigger: BASIC_MEMORY_CLOUD_MODE env var is set
    # Why: cloud deployments must not start local file indexing; cloud handles
    #      file storage via object storage, and local indexing tries to open a
    #      SQLite/Postgres DB that does not exist in the cloud container.
    # Outcome: returns CLOUD mode, skipping file-index initialization.
    cloud_mode = os.getenv("BASIC_MEMORY_CLOUD_MODE", "").lower() in ("1", "true")
    if cloud_mode:
        return RuntimeMode.CLOUD

    return RuntimeMode.LOCAL
