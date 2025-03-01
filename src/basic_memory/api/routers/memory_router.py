"""Routes for memory:// URI operations."""

from typing import Annotated

from dateparser import parse
from fastapi import APIRouter, Query
from loguru import logger

from basic_memory.deps import ContextServiceDep, EntityRepositoryDep
from basic_memory.repository import EntityRepository
from basic_memory.repository.search_repository import SearchIndexRow
from basic_memory.schemas.base import TimeFrame
from basic_memory.schemas.memory import (
    GraphContext,
    RelationSummary,
    EntitySummary,
    ObservationSummary,
    MemoryMetadata,
    normalize_memory_url,
)
from basic_memory.schemas.search import SearchItemType
from basic_memory.services.context_service import ContextResultRow

router = APIRouter(prefix="/memory", tags=["memory"])


async def to_graph_context(context, entity_repository: EntityRepository, page: int, page_size: int):
    # return results
    async def to_summary(item: SearchIndexRow | ContextResultRow):
        match item.type:
            case SearchItemType.ENTITY:
                return EntitySummary(
                    title=item.title,  # pyright: ignore
                    permalink=item.permalink,
                    content=item.content,
                    file_path=item.file_path,
                    created_at=item.created_at,
                )
            case SearchItemType.OBSERVATION:
                return ObservationSummary(
                    title=item.title,  # pyright: ignore
                    file_path=item.file_path,
                    category=item.category,  # pyright: ignore
                    content=item.content,  # pyright: ignore
                    permalink=item.permalink,  # pyright: ignore
                    created_at=item.created_at,
                )
            case SearchItemType.RELATION:
                from_entity = await entity_repository.find_by_id(item.from_id)  # pyright: ignore
                to_entity = await entity_repository.find_by_id(item.to_id) if item.to_id else None
                return RelationSummary(
                    title=item.title,  # pyright: ignore
                    file_path=item.file_path,
                    permalink=item.permalink,  # pyright: ignore
                    relation_type=item.type,
                    from_entity=from_entity.permalink,  # pyright: ignore
                    to_entity=to_entity.permalink if to_entity else None,
                    created_at=item.created_at,
                )
            case _:  # pragma: no cover
                raise ValueError(f"Unexpected type: {item.type}")

    primary_results = [await to_summary(r) for r in context["primary_results"]]
    related_results = [await to_summary(r) for r in context["related_results"]]
    metadata = MemoryMetadata.model_validate(context["metadata"])
    # Transform to GraphContext
    return GraphContext(
        primary_results=primary_results,
        related_results=related_results,
        metadata=metadata,
        page=page,
        page_size=page_size,
    )


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
    since = parse(timeframe)
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
    timeframe: TimeFrame = "7d",
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
    since = parse(timeframe)
    limit = page_size
    offset = (page - 1) * page_size

    # Build context
    context = await context_service.build_context(
        memory_url, depth=depth, since=since, limit=limit, offset=offset, max_related=max_related
    )
    return await to_graph_context(
        context, entity_repository=entity_repository, page=page, page_size=page_size
    )
