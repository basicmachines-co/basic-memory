"""Router for search operations."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, BackgroundTasks

from basic_memory.services.search_service import SearchService
from basic_memory.schemas.search import SearchQuery, SearchResult, SearchResponse
from basic_memory.deps import get_search_service

router = APIRouter(prefix="/search", tags=["search"])


@router.post("/", response_model=SearchResponse)
async def search(query: SearchQuery, search_service: SearchService = Depends(get_search_service)):
    """Search across all knowledge and documents."""
    results = await search_service.search(query)
    search_results = [SearchResult.model_validate(asdict(r)) for r in results]
    return SearchResponse(results=search_results)


@router.post("/reindex")
async def reindex(
    background_tasks: BackgroundTasks, search_service: SearchService = Depends(get_search_service)
):
    """Recreate and populate the search index."""
    await search_service.reindex_all(background_tasks=background_tasks)
    return {"status": "ok", "message": "Reindex initiated"}
