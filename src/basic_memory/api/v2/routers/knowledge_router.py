"""V2 Knowledge Router - ID-based entity operations.

This router provides ID-based CRUD operations for entities, replacing the
path-based identifiers used in v1 with direct integer ID lookups.

Key improvements:
- Direct database lookups via integer primary keys
- Stable references that don't change with file moves
- Better performance through indexed queries
- Simplified caching strategies
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends, Response
from loguru import logger

from basic_memory.deps import (
    EntityServiceV2Dep,
    SearchServiceV2Dep,
    LinkResolverV2Dep,
    ProjectConfigV2Dep,
    AppConfigDep,
    SyncServiceV2Dep,
    EntityRepositoryV2Dep,
    ProjectIdPathDep,
)
from basic_memory.schemas import DeleteEntitiesResponse
from basic_memory.schemas.base import Entity
from basic_memory.schemas.request import EditEntityRequest
from basic_memory.schemas.v2 import (
    EntityResolveRequest,
    EntityResolveResponse,
    EntityResponseV2,
    MoveEntityRequestV2,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge-v2"])


async def resolve_relations_background(sync_service, entity_id: int, entity_permalink: str) -> None:
    """Background task to resolve relations for a specific entity.

    This runs asynchronously after the API response is sent, preventing
    long delays when creating entities with many relations.
    """
    try:
        # Only resolve relations for the newly created entity
        await sync_service.resolve_relations(entity_id=entity_id)
        logger.debug(
            f"Background: Resolved relations for entity {entity_permalink} (id={entity_id})"
        )
    except Exception as e:
        # Log but don't fail - this is a background task
        logger.warning(
            f"Background: Failed to resolve relations for entity {entity_permalink}: {e}"
        )


## Resolution endpoint


@router.post("/resolve", response_model=EntityResolveResponse)
async def resolve_identifier(
    project_id: ProjectIdPathDep,
    data: EntityResolveRequest,
    link_resolver: LinkResolverV2Dep,
) -> EntityResolveResponse:
    """Resolve a string identifier (permalink, title, or path) to an entity ID.

    This endpoint provides a bridge between v1-style identifiers and v2 entity IDs.
    Use this to convert existing references to the new ID-based format.

    Args:
        data: Request containing the identifier to resolve

    Returns:
        Entity ID and metadata about how it was resolved

    Raises:
        HTTPException: 404 if identifier cannot be resolved

    Example:
        POST /v2/{project}/knowledge/resolve
        {"identifier": "specs/search"}

        Returns:
        {
            "entity_id": 123,
            "permalink": "specs/search",
            "file_path": "specs/search.md",
            "title": "Search Specification",
            "resolution_method": "permalink"
        }
    """
    logger.info(f"API v2 request: resolve_identifier for '{data.identifier}'")

    # Try to resolve the identifier
    entity = await link_resolver.resolve_link(data.identifier)
    if not entity:
        raise HTTPException(
            status_code=404, detail=f"Could not resolve identifier: '{data.identifier}'"
        )

    # Determine resolution method
    resolution_method = "search"  # default
    if data.identifier.isdigit():
        resolution_method = "id"
    elif entity.permalink == data.identifier:
        resolution_method = "permalink"
    elif entity.title == data.identifier:
        resolution_method = "title"
    elif entity.file_path == data.identifier:
        resolution_method = "path"

    result = EntityResolveResponse(
        entity_id=entity.id,
        permalink=entity.permalink,
        file_path=entity.file_path,
        title=entity.title,
        resolution_method=resolution_method,
    )

    logger.info(
        f"API v2 response: resolved '{data.identifier}' to entity_id={result.entity_id} via {resolution_method}"
    )

    return result


## Read endpoints


@router.get("/entities/{entity_id}", response_model=EntityResponseV2)
async def get_entity_by_id(
    project_id: ProjectIdPathDep,
    entity_id: int,
    entity_repository: EntityRepositoryV2Dep,
) -> EntityResponseV2:
    """Get an entity by its numeric ID.

    This is the primary entity retrieval method in v2, using direct database
    lookups for maximum performance.

    Args:
        entity_id: Numeric entity ID

    Returns:
        Complete entity with observations and relations

    Raises:
        HTTPException: 404 if entity not found
    """
    logger.info(f"API v2 request: get_entity_by_id entity_id={entity_id}")

    entity = await entity_repository.get_by_id(entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

    result = EntityResponseV2.model_validate(entity)
    logger.info(f"API v2 response: entity_id={entity_id}, title='{result.title}'")

    return result


## Create endpoints


@router.post("/entities", response_model=EntityResponseV2)
async def create_entity(
    project_id: ProjectIdPathDep,
    data: Entity,
    background_tasks: BackgroundTasks,
    entity_service: EntityServiceV2Dep,
    search_service: SearchServiceV2Dep,
) -> EntityResponseV2:
    """Create a new entity.

    Args:
        data: Entity data to create

    Returns:
        Created entity with generated ID
    """
    logger.info(
        "API v2 request", endpoint="create_entity", entity_type=data.entity_type, title=data.title
    )

    entity = await entity_service.create_entity(data)

    # reindex
    await search_service.index_entity(entity, background_tasks=background_tasks)
    result = EntityResponseV2.model_validate(entity)

    logger.info(
        f"API v2 response: endpoint='create_entity' id={entity.id}, title={result.title}, permalink={result.permalink}, status_code=201"
    )
    return result


## Update endpoints


@router.put("/entities/{entity_id}", response_model=EntityResponseV2)
async def update_entity_by_id(
    project_id: ProjectIdPathDep,
    entity_id: int,
    data: Entity,
    response: Response,
    background_tasks: BackgroundTasks,
    entity_service: EntityServiceV2Dep,
    search_service: SearchServiceV2Dep,
    sync_service: SyncServiceV2Dep,
    entity_repository: EntityRepositoryV2Dep,
) -> EntityResponseV2:
    """Update an entity by ID.

    If the entity doesn't exist, it will be created (upsert behavior).

    Args:
        entity_id: Numeric entity ID
        data: Updated entity data

    Returns:
        Updated entity
    """
    logger.info(f"API v2 request: update_entity_by_id entity_id={entity_id}")

    # Check if entity exists
    existing = await entity_repository.get_by_id(entity_id)
    created = existing is None

    # Perform update or create
    entity, _ = await entity_service.create_or_update_entity(data)
    response.status_code = 201 if created else 200

    # reindex
    await search_service.index_entity(entity, background_tasks=background_tasks)

    # Schedule relation resolution for new entities
    if created:
        background_tasks.add_task(
            resolve_relations_background, sync_service, entity.id, entity.permalink or ""
        )

    result = EntityResponseV2.model_validate(entity)

    logger.info(
        f"API v2 response: entity_id={entity_id}, created={created}, status_code={response.status_code}"
    )
    return result


@router.patch("/entities/{entity_id}", response_model=EntityResponseV2)
async def edit_entity_by_id(
    project_id: ProjectIdPathDep,
    entity_id: int,
    data: EditEntityRequest,
    background_tasks: BackgroundTasks,
    entity_service: EntityServiceV2Dep,
    search_service: SearchServiceV2Dep,
    entity_repository: EntityRepositoryV2Dep,
) -> EntityResponseV2:
    """Edit an existing entity by ID using operations like append, prepend, etc.

    Args:
        entity_id: Numeric entity ID
        data: Edit operation details

    Returns:
        Updated entity

    Raises:
        HTTPException: 404 if entity not found, 400 if edit fails
    """
    logger.info(
        f"API v2 request: edit_entity_by_id entity_id={entity_id}, operation='{data.operation}'"
    )

    # Verify entity exists
    entity = await entity_repository.get_by_id(entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

    try:
        # Edit using the entity's permalink or path
        identifier = entity.permalink or entity.file_path
        updated_entity = await entity_service.edit_entity(
            identifier=identifier,
            operation=data.operation,
            content=data.content,
            section=data.section,
            find_text=data.find_text,
            expected_replacements=data.expected_replacements,
        )

        # Reindex
        await search_service.index_entity(updated_entity, background_tasks=background_tasks)

        result = EntityResponseV2.model_validate(updated_entity)

        logger.info(
            f"API v2 response: entity_id={entity_id}, operation='{data.operation}', status_code=200"
        )

        return result

    except Exception as e:
        logger.error(f"Error editing entity {entity_id}: {e}")
        raise HTTPException(status_code=400, detail=str(e))


## Delete endpoints


@router.delete("/entities/{entity_id}", response_model=DeleteEntitiesResponse)
async def delete_entity_by_id(
    project_id: ProjectIdPathDep,
    entity_id: int,
    background_tasks: BackgroundTasks,
    entity_service: EntityServiceV2Dep,
    entity_repository: EntityRepositoryV2Dep,
    search_service=Depends(lambda: None),  # Optional for now
) -> DeleteEntitiesResponse:
    """Delete an entity by ID.

    Args:
        entity_id: Numeric entity ID

    Returns:
        Deletion status

    Note: Returns deleted=False if entity doesn't exist (idempotent)
    """
    logger.info(f"API v2 request: delete_entity_by_id entity_id={entity_id}")

    entity = await entity_repository.get_by_id(entity_id)
    if entity is None:
        logger.info(f"API v2 response: entity_id={entity_id} not found, deleted=False")
        return DeleteEntitiesResponse(deleted=False)

    # Delete the entity
    deleted = await entity_service.delete_entity(entity_id)

    # Remove from search index if search service available
    if search_service:
        background_tasks.add_task(search_service.handle_delete, entity)

    logger.info(f"API v2 response: entity_id={entity_id}, deleted={deleted}")

    return DeleteEntitiesResponse(deleted=deleted)


## Move endpoint


@router.put("/entities/{entity_id}/move", response_model=EntityResponseV2)
async def move_entity(
    project_id: ProjectIdPathDep,
    entity_id: int,
    data: MoveEntityRequestV2,
    background_tasks: BackgroundTasks,
    entity_service: EntityServiceV2Dep,
    entity_repository: EntityRepositoryV2Dep,
    project_config: ProjectConfigV2Dep,
    app_config: AppConfigDep,
    search_service: SearchServiceV2Dep,
) -> EntityResponseV2:
    """Move an entity to a new file location.

    V2 API uses entity ID in the URL path for stable references.
    The entity ID will remain stable after the move.

    Args:
        project_id: Project ID from URL path
        entity_id: Entity ID from URL path (primary identifier)
        data: Move request with destination path only

    Returns:
        Updated entity with new file path
    """
    logger.info(
        f"API v2 request: move_entity entity_id={entity_id}, destination='{data.destination_path}'"
    )

    try:
        # First, get the entity by ID to verify it exists
        entity = await entity_repository.find_by_id(entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail=f"Entity not found: {entity_id}")

        # Move the entity using its current file path as identifier
        moved_entity = await entity_service.move_entity(
            identifier=entity.file_path,  # Use file path for resolution
            destination_path=data.destination_path,
            project_config=project_config,
            app_config=app_config,
        )

        # Reindex at new location
        reindexed_entity = await entity_service.link_resolver.resolve_link(data.destination_path)
        if reindexed_entity:
            await search_service.index_entity(reindexed_entity, background_tasks=background_tasks)

        result = EntityResponseV2.model_validate(moved_entity)

        logger.info(
            f"API v2 response: moved entity_id={moved_entity.id} to '{data.destination_path}'"
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error moving entity: {e}")
        raise HTTPException(status_code=400, detail=str(e))
