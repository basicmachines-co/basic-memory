"""API v2 module - ID-based entity references.

Version 2 of the Basic Memory API uses integer entity IDs as the primary
identifier for improved performance and stability.

Key changes from v1:
- Entity lookups use integer IDs instead of paths/permalinks
- Direct database queries instead of cascading resolution
- Stable references that don't change with file moves
- Better caching support

All v2 routers are registered with the /v2 prefix.
"""

from basic_memory.api.v2.routers import (
    knowledge_router,
    memory_router,
    project_router,
    resource_router,
    search_router,
    directory_router,
    prompt_router,
    importer_router,
)

__all__ = [
    "knowledge_router",
    "memory_router",
    "project_router",
    "resource_router",
    "search_router",
    "directory_router",
    "prompt_router",
    "importer_router",
]
