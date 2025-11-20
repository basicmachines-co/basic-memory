"""V2 API routers."""

from basic_memory.api.v2.routers.knowledge_router import router as knowledge_router
from basic_memory.api.v2.routers.project_router import router as project_router

__all__ = ["knowledge_router", "project_router"]
