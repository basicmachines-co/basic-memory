"""Router for knowledge graph operations."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends, Query, Response
from loguru import logger

from basic_memory.deps import (
    EntityServiceDep,
    get_search_service,
    SearchServiceDep,
    LinkResolverDep,
    ProjectPathDep,
    FileServiceDep,
    ProjectConfigDep,
    AppConfigDep,
)
from basic_memory.schemas import (
    EntityListResponse,
    EntityResponse,
    DeleteEntitiesResponse,
    DeleteEntitiesRequest,
)
from basic_memory.schemas.request import EditEntityRequest, MoveEntityRequest
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
        f"API response: endpoint='create_entity' title={result.title}, permalink={result.permalink}, status_code=201"
    )
    return result


@router.put("/entities/{permalink:path}", response_model=EntityResponse)
async def create_or_update_entity(
    project: ProjectPathDep,
    permalink: Permalink,
    data: Entity,
    response: Response,
    background_tasks: BackgroundTasks,
    entity_service: EntityServiceDep,
    search_service: SearchServiceDep,
    file_service: FileServiceDep,
) -> EntityResponse:
    """Create or update an entity. If entity exists, it will be updated, otherwise created."""
    logger.info(
        f"API request: create_or_update_entity for {project=}, {permalink=}, {data.entity_type=}, {data.title=}"
    )

    # Validate permalink matches
    if data.permalink != permalink:
        logger.warning(
            f"API validation error: creating/updating entity with permalink mismatch - url={permalink}, data={data.permalink}",
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
        f"API response: {result.title=}, {result.permalink=}, {created=}, status_code={response.status_code}"
    )
    return result


@router.patch("/entities/{identifier:path}", response_model=EntityResponse)
async def edit_entity(
    identifier: str,
    data: EditEntityRequest,
    background_tasks: BackgroundTasks,
    entity_service: EntityServiceDep,
    search_service: SearchServiceDep,
) -> EntityResponse:
    """Edit an existing entity using various operations like append, prepend, find_replace, or replace_section.

    This endpoint allows for targeted edits without requiring the full entity content.
    """
    logger.info(
        f"API request: endpoint='edit_entity', identifier='{identifier}', operation='{data.operation}'"
    )

    try:
        # Edit the entity using the service
        entity = await entity_service.edit_entity(
            identifier=identifier,
            operation=data.operation,
            content=data.content,
            section=data.section,
            find_text=data.find_text,
            expected_replacements=data.expected_replacements,
        )

        # Reindex the updated entity
        await search_service.index_entity(entity, background_tasks=background_tasks)

        # Return the updated entity response
        result = EntityResponse.model_validate(entity)

        logger.info(
            "API response",
            endpoint="edit_entity",
            identifier=identifier,
            operation=data.operation,
            permalink=result.permalink,
            status_code=200,
        )

        return result

    except Exception as e:
        logger.error(f"Error editing entity: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/move")
async def move_entity(
    data: MoveEntityRequest,
    background_tasks: BackgroundTasks,
    entity_service: EntityServiceDep,
    project_config: ProjectConfigDep,
    app_config: AppConfigDep,
    search_service: SearchServiceDep,
) -> EntityResponse:
    """Move an entity to a new file location with project consistency.

    This endpoint moves a note to a different path while maintaining project
    consistency and optionally updating permalinks based on configuration.
    """
    logger.info(
        f"API request: endpoint='move_entity', identifier='{data.identifier}', destination='{data.destination_path}'"
    )

    try:
        # Move the entity using the service
        moved_entity = await entity_service.move_entity(
            identifier=data.identifier,
            destination_path=data.destination_path,
            project_config=project_config,
            app_config=app_config,
        )

        # Get the moved entity to reindex it
        entity = await entity_service.link_resolver.resolve_link(data.destination_path)
        if entity:
            await search_service.index_entity(entity, background_tasks=background_tasks)

        logger.info(
            "API response",
            endpoint="move_entity",
            identifier=data.identifier,
            destination=data.destination_path,
            status_code=200,
        )
        result = EntityResponse.model_validate(moved_entity)
        return result

    except Exception as e:
        logger.error(f"Error moving entity: {e}")
        raise HTTPException(status_code=400, detail=str(e))


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

    # Remove from search index (entity, observations, and relations)
    background_tasks.add_task(search_service.handle_delete, entity)

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