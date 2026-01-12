"""Configuration dependency injection for basic-memory.

This module provides configuration-related dependencies.
Note: Long-term goal is to minimize direct ConfigManager access
and inject config from composition roots instead.
"""

from typing import Annotated

from fastapi import Depends

from basic_memory.config import BasicMemoryConfig, ConfigManager


def get_app_config() -> BasicMemoryConfig:  # pragma: no cover
    """Get the application configuration.

    Note: This is a transitional dependency. The goal is for composition roots
    to read ConfigManager and inject config explicitly. During migration,
    this provides the same behavior as before.
    """
    app_config = ConfigManager().config
    return app_config


AppConfigDep = Annotated[BasicMemoryConfig, Depends(get_app_config)]
