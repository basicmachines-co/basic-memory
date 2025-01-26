"""Router for knowledge graph operations."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends, Query, Response
from loguru import logger

from basic_memory.deps import (
    EntityServiceDep,
    get_search_service,
    RelationServiceDep,
    ObservationServiceDep,
    SearchServiceDep, LinkResolverDep,
)
from basic_memory.schemas import (
    CreateEntityRequest,
    EntityListResponse,
    CreateRelationsRequest,
    EntityResponse,
    AddObservationsRequest,
    DeleteEntitiesResponse,
    DeleteObservationsRequest,
    DeleteRelationsRequest,
    DeleteEntitiesRequest,
)
from basic_memory.schemas.base import PathId, Entity
from basic_memory.services.exceptions import EntityNotFoundError

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

## Create endpoints


@router.put("/entities/{permalink:path}", response_model=EntityResponse)
async def create_or_update_entity(
    permalink: PathId,
    data: Entity,
    response: Response,
    background_tasks: BackgroundTasks,
    entity_service: EntityServiceDep,
    search_service: SearchServiceDep,
) -> EntityResponse:
    """Create or update an entity. If entity exists, it will be updated, otherwise created."""
    # Validate permalink matches
    if data.permalink != permalink:
        raise HTTPException(status_code=400, detail="Entity permalink must match URL path")

    # Try create_or_update operation
    entity, created = await entity_service.create_or_update_entity(data)
    response.status_code = 201 if created else 200

    # reindex
    await search_service.index_entity(entity, background_tasks=background_tasks)

    return EntityResponse.model_validate(entity)


@router.post("/entities", response_model=EntityListResponse)
async def create_entities(
    data: CreateEntityRequest,
    background_tasks: BackgroundTasks,
    entity_service: EntityServiceDep,
    search_service=Depends(get_search_service),
) -> EntityListResponse:
    """Create new entities in the knowledge graph and index them."""
    entities = await entity_service.create_entities(data.entities)

    # Index each entity
    for entity in entities:
        await search_service.index_entity(entity, background_tasks=background_tasks)

    return EntityListResponse(
        entities=[EntityResponse.model_validate(entity) for entity in entities]
    )


@router.post("/relations", response_model=EntityListResponse)
async def create_relations(
    data: CreateRelationsRequest,
    background_tasks: BackgroundTasks,
    relation_service: RelationServiceDep,
    search_service=Depends(get_search_service),
) -> EntityListResponse:
    """Create relations between entities and update search index."""
    updated_entities = await relation_service.create_relations(data.relations)

    # Reindex updated entities since relations have changed
    for entity in updated_entities:
        await search_service.index_entity(entity, background_tasks=background_tasks)

    return EntityListResponse(
        entities=[EntityResponse.model_validate(entity) for entity in updated_entities]
    )


@router.post("/observations", response_model=EntityResponse)
async def add_observations(
    data: AddObservationsRequest,
    background_tasks: BackgroundTasks,
    observation_service: ObservationServiceDep,
    search_service=Depends(get_search_service),
) -> EntityResponse:
    """Add observations to an entity and update search index."""
    logger.debug(f"Adding observations to entity: {data.permalink}")
    updated_entity = await observation_service.add_observations(
        data.permalink, data.observations, data.context
    )

    # Reindex the entity with new observations
    await search_service.index_entity(updated_entity, background_tasks=background_tasks)

    return EntityResponse.model_validate(updated_entity)


## Read endpoints


@router.get("/entities/{permalink:path}", response_model=EntityResponse)
async def get_entity(
    entity_service: EntityServiceDep,
    permalink: str,
) -> EntityResponse:
    """Get a specific entity by ID.

    Args:
        permalink: Entity path ID
        content: If True, include full file content
        :param entity_service: EntityService
    """
    try:
        entity = await entity_service.get_by_permalink(permalink)
        entity_response = EntityResponse.model_validate(entity)
        return entity_response
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail=f"Entity with {permalink} not found")


@router.get("/entities", response_model=EntityListResponse)
async def get_entities(
    entity_service: EntityServiceDep,
    permalink: Annotated[list[str] | None, Query()] = None,
) -> EntityListResponse:
    """Open specific entities"""
    # permalink is a list of parameters on the request ?permalink=foo
    entities = await entity_service.get_entities_by_permalinks(permalink)
    return EntityListResponse(
        entities=[EntityResponse.model_validate(entity) for entity in entities]
    )


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
    
    entity = await link_resolver.resolve_link(identifier)
    if entity is None:
        return DeleteEntitiesResponse(deleted=False)
    
    # Delete the entity
    deleted = await entity_service.delete_entity(entity.permalink)

    # Remove from search index
    background_tasks.add_task(search_service.delete_by_permalink, entity.permalink)

    return DeleteEntitiesResponse(deleted=deleted)


@router.post("/entities/delete", response_model=DeleteEntitiesResponse)
async def delete_entities(
    data: DeleteEntitiesRequest,
    background_tasks: BackgroundTasks,
    entity_service: EntityServiceDep,
    search_service=Depends(get_search_service),
) -> DeleteEntitiesResponse:
    """Delete entities and remove from search index."""
    deleted = await entity_service.delete_entities(data.permalinks)

    # Remove each deleted entity from search index
    for permalink in data.permalinks:
        background_tasks.add_task(search_service.delete_by_permalink, permalink)

    return DeleteEntitiesResponse(deleted=deleted)


@router.post("/observations/delete", response_model=EntityResponse)
async def delete_observations(
    data: DeleteObservationsRequest,
    background_tasks: BackgroundTasks,
    observation_service: ObservationServiceDep,
    search_service=Depends(get_search_service),
) -> EntityResponse:
    """Delete observations and update search index."""
    permalink = data.permalink
    updated_entity = await observation_service.delete_observations(permalink, data.observations)

    # Reindex the entity since observations changed
    await search_service.index_entity(updated_entity, background_tasks=background_tasks)

    return EntityResponse.model_validate(updated_entity)


@router.post("/relations/delete", response_model=EntityListResponse)
async def delete_relations(
    data: DeleteRelationsRequest,
    background_tasks: BackgroundTasks,
    relation_service: RelationServiceDep,
    search_service=Depends(get_search_service),
) -> EntityListResponse:
    """Delete relations and update search index."""
    updated_entities = await relation_service.delete_relations(data.relations)

    # Reindex entities since relations changed
    for entity in updated_entities:
        await search_service.index_entity(entity, background_tasks=background_tasks)

    return EntityListResponse(
        entities=[EntityResponse.model_validate(entity) for entity in updated_entities]
    )
