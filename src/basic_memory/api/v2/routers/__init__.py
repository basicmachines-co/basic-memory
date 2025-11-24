"""V2 API routers."""

from basic_memory.api.v2.routers.knowledge_router import router as knowledge_router
from basic_memory.api.v2.routers.project_router import router as project_router
from basic_memory.api.v2.routers.memory_router import router as memory_router
from basic_memory.api.v2.routers.search_router import router as search_router
from basic_memory.api.v2.routers.resource_router import router as resource_router
from basic_memory.api.v2.routers.directory_router import router as directory_router
from basic_memory.api.v2.routers.prompt_router import router as prompt_router
from basic_memory.api.v2.routers.importer_router import router as importer_router

__all__ = [
    "knowledge_router",
    "project_router",
    "memory_router",
    "search_router",
    "resource_router",
    "directory_router",
    "prompt_router",
    "importer_router",
]
