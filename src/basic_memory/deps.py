"""Dependency injection functions for basic-memory services.

DEPRECATED: This module is a backwards-compatibility shim.
Import from basic_memory.deps package submodules instead:
- basic_memory.deps.config for configuration
- basic_memory.deps.db for database/session
- basic_memory.deps.projects for project resolution
- basic_memory.deps.repositories for data access
- basic_memory.deps.services for business logic
- basic_memory.deps.importers for import functionality

This file will be removed once all callers are migrated.
"""

# Re-export everything from the deps package for backwards compatibility
from basic_memory.deps import *  # noqa: F401, F403  # pragma: no cover
