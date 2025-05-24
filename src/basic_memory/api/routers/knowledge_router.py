"""Router for knowledge graph operations."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends, Query, Response
from loguru import logger

from basic_memory.deps import (
    EntityServiceDep,
    get_search_service,
    SearchServiceDep,
    LinkResolverDep,
)
from basic_memory.schemas import (
    EntityListResponse,
    EntityResponse,
    DeleteEntitiesResponse,
    DeleteEntitiesRequest,
)
from basic_memory.schemas.base import Permalink, Entity

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

## Create endpoints


@router.post("/entities", response_model=EntityResponse)
async def create_entity(
    data: Entity,
    background_tasks: BackgroundTasks,
    entity_service: EntityServiceDep,
    search_service: SearchServiceDep,
) -> EntityResponse:
    """Create an entity."""
    logger.info(
        "API request", endpoint="create_entity", entity_type=data.entity_type, title=data.title
    )

    entity = await entity_service.create_entity(data)

    # reindex
    await search_service.index_entity(entity, background_tasks=background_tasks)
    result = EntityResponse.model_validate(entity)

    logger.info(
        "API response",
        endpoint="create_entity",
        title=result.title,
        permalink=result.permalink,
        status_code=201,
    )
    return result


@router.put("/entities/{permalink:path}", response_model=EntityResponse)
async def create_or_update_entity(
    permalink: Permalink,
    data: Entity,
    response: Response,
    background_tasks: BackgroundTasks,
    entity_service: EntityServiceDep,
    search_service: SearchServiceDep,
) -> EntityResponse:
    """Create or update an entity. If entity exists, it will be updated, otherwise created."""
    logger.info(
        "API request",
        endpoint="create_or_update_entity",
        permalink=permalink,
        entity_type=data.entity_type,
        title=data.title,
    )

    # Validate permalink matches
    if data.permalink != permalink:
        logger.warning(
            "API validation error",
            endpoint="create_or_update_entity",
            permalink=permalink,
            data_permalink=data.permalink,
            error="Permalink mismatch",
        )
        raise HTTPException(
            status_code=400,
            detail=f"Entity permalink {data.permalink} must match URL path: '{permalink}'",
        )

    # Try create_or_update operation
    entity, created = await entity_service.create_or_update_entity(data)
    response.status_code = 201 if created else 200

    # reindex
    await search_service.index_entity(entity, background_tasks=background_tasks)
    result = EntityResponse.model_validate(entity)

    logger.info(
        "API response",
        endpoint="create_or_update_entity",
        title=result.title,
        permalink=result.permalink,
        created=created,
        status_code=response.status_code,
    )
    return result


## Read endpoints


@router.get("/entities/{identifier:path}", response_model=EntityResponse)
async def get_entity(
    entity_service: EntityServiceDep,
    link_resolver: LinkResolverDep,
    identifier: str,
) -> EntityResponse:
    """Get a specific entity by file path or permalink..

    Args:
        identifier: Entity file path or permalink
        :param entity_service: EntityService
        :param link_resolver: LinkResolver
    """
    logger.info(f"request: get_entity with identifier={identifier}")
    entity = await link_resolver.resolve_link(identifier)
    if not entity:
        raise HTTPException(status_code=404, detail=f"Entity {identifier} not found")

    result = EntityResponse.model_validate(entity)
    return result


@router.get("/entities", response_model=EntityListResponse)
async def get_entities(
    entity_service: EntityServiceDep,
    permalink: Annotated[list[str] | None, Query()] = None,
) -> EntityListResponse:
    """Open specific entities"""
    logger.info(f"request: get_entities with permalinks={permalink}")

    entities = await entity_service.get_entities_by_permalinks(permalink) if permalink else []
    result = EntityListResponse(
        entities=[EntityResponse.model_validate(entity) for entity in entities]
    )
    return result


## Delete endpoints


@router.delete("/entities/{identifier:path}", response_model=DeleteEntitiesResponse)
async def delete_entity(
    identifier: str,
    background_tasks: BackgroundTasks,
    entity_service: EntityServiceDep,
    link_resolver: LinkResolverDep,
    search_service=Depends(get_search_service),
) -> DeleteEntitiesResponse:
    """Delete a single entity and remove from search index."""
    logger.info(f"request: delete_entity with identifier={identifier}")

    entity = await link_resolver.resolve_link(identifier)
    if entity is None:
        return DeleteEntitiesResponse(deleted=False)

    # Delete the entity
    deleted = await entity_service.delete_entity(entity.permalink or entity.id)

    # Remove from search index
    background_tasks.add_task(search_service.delete_by_permalink, entity.permalink)

    result = DeleteEntitiesResponse(deleted=deleted)
    return result


@router.post("/entities/delete", response_model=DeleteEntitiesResponse)
async def delete_entities(
    data: DeleteEntitiesRequest,
    background_tasks: BackgroundTasks,
    entity_service: EntityServiceDep,
    search_service=Depends(get_search_service),
) -> DeleteEntitiesResponse:
    """Delete entities and remove from search index."""
    logger.info(f"request: delete_entities with data={data}")
    deleted = False

    # Remove each deleted entity from search index
    for permalink in data.permalinks:
        deleted = await entity_service.delete_entity(permalink)
        background_tasks.add_task(search_service.delete_by_permalink, permalink)

    result = DeleteEntitiesResponse(deleted=deleted)
    return result
