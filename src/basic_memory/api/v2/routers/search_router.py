"""V2 router for search operations.

This router uses external_id UUIDs for stable, API-friendly routing.
V1 uses string-based project names which are less efficient and less stable.
"""

from fastapi import APIRouter, Path

from basic_memory.api.v2.utils import to_search_results
from basic_memory.schemas.search import SearchQuery, SearchResponse
from basic_memory.deps import (
    SearchServiceV2ExternalDep,
    EntityServiceV2ExternalDep,
    TaskSchedulerDep,
    ProjectExternalIdPathDep,
)

# Note: No prefix here - it's added during registration as /v2/{project_id}/search
router = APIRouter(tags=["search"])


@router.post("/search/", response_model=SearchResponse)
async def search(
    query: SearchQuery,
    search_service: SearchServiceV2ExternalDep,
    entity_service: EntityServiceV2ExternalDep,
    project_id: str = Path(..., description="Project external UUID"),
    page: int = 1,
    page_size: int = 10,
):
    """Search across all knowledge and documents in a project.

    V2 uses external_id UUIDs for stable API references.

    Args:
        project_id: Project external UUID from URL path
        query: Search query parameters (text, filters, etc.)
        search_service: Search service scoped to project
        entity_service: Entity service scoped to project
        page: Page number for pagination
        page_size: Number of results per page

    Returns:
        SearchResponse with paginated search results
    """
    limit = page_size
    offset = (page - 1) * page_size
    results = await search_service.search(query, limit=limit, offset=offset)
    search_results = await to_search_results(entity_service, results)
    return SearchResponse(
        results=search_results,
        current_page=page,
        page_size=page_size,
    )


@router.post("/search/reindex")
async def reindex(
    task_scheduler: TaskSchedulerDep,
    project_id: ProjectExternalIdPathDep,
):
    """Recreate and populate the search index for a project.

    This is a background operation that rebuilds the search index
    from scratch. Useful after bulk updates or if the index becomes
    corrupted.

    Args:
        project_id: Project external UUID from URL path
        task_scheduler: Task scheduler for background work

    Returns:
        Status message indicating reindex has been initiated
    """
    task_scheduler.schedule("reindex_project", project_id=project_id)
    return {"status": "ok", "message": "Reindex initiated"}
