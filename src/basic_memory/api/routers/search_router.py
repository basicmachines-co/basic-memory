"""Router for search operations."""

from fastapi import APIRouter, BackgroundTasks

from basic_memory.api.routers.utils import to_search_results
from basic_memory.schemas.search import SearchQuery, SearchResponse
from basic_memory.deps import SearchServiceDep, EntityServiceDep

router = APIRouter(prefix="/search", tags=["search"])


@router.post("/", response_model=SearchResponse)
async def search(
    query: SearchQuery,
    search_service: SearchServiceDep,
    entity_service: EntityServiceDep,
    page: int = 1,
    page_size: int = 10,
):
    """Search across all knowledge and documents."""
    limit = page_size
    offset = (page - 1) * page_size
    results = await search_service.search(query, limit=limit, offset=offset)
    search_results = await to_search_results(entity_service, results)
    return SearchResponse(
        results=search_results,
        current_page=page,
        page_size=page_size,
    )


@router.post("/reindex")
async def reindex(background_tasks: BackgroundTasks, search_service: SearchServiceDep):
    """Recreate and populate the search index."""
    await search_service.reindex_all(background_tasks=background_tasks)
    return {"status": "ok", "message": "Reindex initiated"}
