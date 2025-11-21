"""V2 routes for memory:// URI operations.

This router uses integer project IDs for stable, efficient routing.
V1 uses string-based project names which are less efficient and less stable.
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Query
from loguru import logger

from basic_memory.deps import ContextServiceV2Dep, EntityRepositoryV2Dep, ProjectIdPathDep
from basic_memory.schemas.base import TimeFrame, parse_timeframe
from basic_memory.schemas.memory import (
    GraphContext,
    normalize_memory_url,
)
from basic_memory.schemas.search import SearchItemType
from basic_memory.api.routers.utils import to_graph_context

# Note: No prefix here - it's added during registration as /v2/{project_id}/memory
router = APIRouter(tags=["memory"])


@router.get("/memory/recent", response_model=GraphContext)
async def recent(
    project_id: ProjectIdPathDep,
    context_service: ContextServiceV2Dep,
    entity_repository: EntityRepositoryV2Dep,
    type: Annotated[list[SearchItemType] | None, Query()] = None,
    depth: int = 1,
    timeframe: TimeFrame = "7d",
    page: int = 1,
    page_size: int = 10,
    max_related: int = 10,
) -> GraphContext:
    """Get recent activity context for a project.

    Args:
        project_id: Validated numeric project ID from URL path
        context_service: Context service scoped to project
        entity_repository: Entity repository scoped to project
        type: Types of items to include (entities, relations, observations)
        depth: How many levels of related entities to include
        timeframe: Time window for recent activity (e.g., "7d", "1 week")
        page: Page number for pagination
        page_size: Number of items per page
        max_related: Maximum related entities to include per item

    Returns:
        GraphContext with recent activity and related entities
    """
    # return all types by default
    types = (
        [SearchItemType.ENTITY, SearchItemType.RELATION, SearchItemType.OBSERVATION]
        if not type
        else type
    )

    logger.debug(
        f"V2 Getting recent context for project {project_id}: `{types}` depth: `{depth}` timeframe: `{timeframe}` page: `{page}` page_size: `{page_size}` max_related: `{max_related}`"
    )
    # Parse timeframe
    since = parse_timeframe(timeframe)
    limit = page_size
    offset = (page - 1) * page_size

    # Build context
    context = await context_service.build_context(
        types=types, depth=depth, since=since, limit=limit, offset=offset, max_related=max_related
    )
    recent_context = await to_graph_context(
        context, entity_repository=entity_repository, page=page, page_size=page_size
    )
    logger.debug(f"V2 Recent context: {recent_context.model_dump_json()}")
    return recent_context


# get_memory_context needs to be declared last so other paths can match


@router.get("/memory/{uri:path}", response_model=GraphContext)
async def get_memory_context(
    project_id: ProjectIdPathDep,
    context_service: ContextServiceV2Dep,
    entity_repository: EntityRepositoryV2Dep,
    uri: str,
    depth: int = 1,
    timeframe: Optional[TimeFrame] = None,
    page: int = 1,
    page_size: int = 10,
    max_related: int = 10,
) -> GraphContext:
    """Get rich context from memory:// URI.

    V2 supports both legacy path-based URIs and new ID-based URIs:
    - Legacy: memory://path/to/note
    - ID-based: memory://id/123 or memory://123

    Args:
        project_id: Validated numeric project ID from URL path
        context_service: Context service scoped to project
        entity_repository: Entity repository scoped to project
        uri: Memory URI path (e.g., "id/123", "123", or "path/to/note")
        depth: How many levels of related entities to include
        timeframe: Optional time window for filtering related content
        page: Page number for pagination
        page_size: Number of items per page
        max_related: Maximum related entities to include

    Returns:
        GraphContext with the entity and its related context
    """
    logger.debug(
        f"V2 Getting context for project {project_id}, URI: `{uri}` depth: `{depth}` timeframe: `{timeframe}` page: `{page}` page_size: `{page_size}` max_related: `{max_related}`"
    )
    memory_url = normalize_memory_url(uri)

    # Parse timeframe
    since = parse_timeframe(timeframe) if timeframe else None
    limit = page_size
    offset = (page - 1) * page_size

    # Build context
    context = await context_service.build_context(
        memory_url, depth=depth, since=since, limit=limit, offset=offset, max_related=max_related
    )
    return await to_graph_context(
        context, entity_repository=entity_repository, page=page, page_size=page_size
    )
