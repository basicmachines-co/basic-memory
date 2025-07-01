"""Routes for memory:// URI operations."""

from typing import Annotated, Optional

from fastapi import APIRouter, Query
from loguru import logger

from basic_memory.deps import ContextServiceDep, EntityRepositoryDep
from basic_memory.schemas.base import TimeFrame, parse_timeframe
from basic_memory.schemas.memory import (
    GraphContext,
    normalize_memory_url,
)
from basic_memory.schemas.search import SearchItemType
from basic_memory.api.routers.utils import to_graph_context

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/recent", response_model=GraphContext)
async def recent(
    context_service: ContextServiceDep,
    entity_repository: EntityRepositoryDep,
    type: Annotated[list[SearchItemType] | None, Query()] = None,
    depth: int = 1,
    timeframe: TimeFrame = "7d",
    page: int = 1,
    page_size: int = 10,
    max_related: int = 10,
) -> GraphContext:
    # return all types by default
    types = (
        [SearchItemType.ENTITY, SearchItemType.RELATION, SearchItemType.OBSERVATION]
        if not type
        else type
    )

    logger.debug(
        f"Getting recent context: `{types}` depth: `{depth}` timeframe: `{timeframe}` page: `{page}` page_size: `{page_size}` max_related: `{max_related}`"
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
    logger.debug(f"Recent context: {recent_context.model_dump_json()}")
    return recent_context


# get_memory_context needs to be declared last so other paths can match


@router.get("/{uri:path}", response_model=GraphContext)
async def get_memory_context(
    context_service: ContextServiceDep,
    entity_repository: EntityRepositoryDep,
    uri: str,
    depth: int = 1,
    timeframe: Optional[TimeFrame] = None,
    page: int = 1,
    page_size: int = 10,
    max_related: int = 10,
) -> GraphContext:
    """Get rich context from memory:// URI."""
    # add the project name from the config to the url as the "host
    # Parse URI
    logger.debug(
        f"Getting context for URI: `{uri}` depth: `{depth}` timeframe: `{timeframe}` page: `{page}` page_size: `{page_size}` max_related: `{max_related}`"
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
