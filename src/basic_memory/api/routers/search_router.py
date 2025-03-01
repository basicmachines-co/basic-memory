"""Router for search operations."""

from fastapi import APIRouter, BackgroundTasks

from basic_memory.schemas.search import SearchQuery, SearchResult, SearchResponse
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

    search_results = []
    for r in results:
        entities = await entity_service.get_entities_by_id([r.entity_id, r.from_id, r.to_id])  # pyright: ignore
        search_results.append(
            SearchResult(
                title=r.title,  # pyright: ignore
                type=r.type,  # pyright: ignore
                permalink=r.permalink,
                score=r.score,  # pyright: ignore
                entity=entities[0].permalink if entities else None,
                content=r.content,
                file_path=r.file_path,
                metadata=r.metadata,
                category=r.category,
                from_entity=entities[0].permalink if entities else None,
                to_entity=entities[1].permalink if len(entities) > 1 else None,
                relation_type=r.relation_type,
            )
        )
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
