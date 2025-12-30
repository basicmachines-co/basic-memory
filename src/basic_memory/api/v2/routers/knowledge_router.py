"""V2 Knowledge Router - External ID-based entity operations.

This router provides external_id (UUID) based CRUD operations for entities,
using stable string UUIDs that won't change with file moves or database migrations.

Key improvements:
- Stable external UUIDs that won't change with file moves or renames
- Better API ergonomics with consistent string identifiers
- Direct database lookups via unique indexed column
- Simplified caching strategies
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends, Response, Path
from loguru import logger

from basic_memory.deps import (
    EntityServiceV2ExternalDep,
    SearchServiceV2ExternalDep,
    LinkResolverV2ExternalDep,
    ProjectConfigV2ExternalDep,
    AppConfigDep,
    SyncServiceV2ExternalDep,
    EntityRepositoryV2ExternalDep,
    ProjectExternalIdPathDep,
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
    project_id: ProjectExternalIdPathDep,
    data: EntityResolveRequest,
    link_resolver: LinkResolverV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
) -> EntityResolveResponse:
    """Resolve a string identifier (external_id, permalink, title, or path) to entity info.

    This endpoint provides a bridge between v1-style identifiers and v2 external_ids.
    Use this to convert existing references to the new UUID-based format.

    Args:
        data: Request containing the identifier to resolve

    Returns:
        Entity external_id and metadata about how it was resolved

    Raises:
        HTTPException: 404 if identifier cannot be resolved

    Example:
        POST /v2/{project_id}/knowledge/resolve
        {"identifier": "specs/search"}

        Returns:
        {
            "external_id": "550e8400-e29b-41d4-a716-446655440000",
            "entity_id": 123,
            "permalink": "specs/search",
            "file_path": "specs/search.md",
            "title": "Search Specification",
            "resolution_method": "permalink"
        }
    """
    logger.info(f"API v2 request: resolve_identifier for '{data.identifier}'")

    # Try to resolve by external_id first
    entity = await entity_repository.get_by_external_id(data.identifier)
    resolution_method = "external_id" if entity else "search"

    # If not found by external_id, try other resolution methods
    if not entity:
        entity = await link_resolver.resolve_link(data.identifier)
        if entity:
            # Determine resolution method
            if entity.permalink == data.identifier:
                resolution_method = "permalink"
            elif entity.title == data.identifier:
                resolution_method = "title"
            elif entity.file_path == data.identifier:
                resolution_method = "path"
            else:
                resolution_method = "search"

    if not entity:
        raise HTTPException(status_code=404, detail=f"Entity not found: '{data.identifier}'")

    result = EntityResolveResponse(
        external_id=entity.external_id,
        entity_id=entity.id,
        permalink=entity.permalink,
        file_path=entity.file_path,
        title=entity.title,
        resolution_method=resolution_method,
    )

    logger.info(
        f"API v2 response: resolved '{data.identifier}' to external_id={result.external_id} via {resolution_method}"
    )

    return result


## Read endpoints


@router.get("/entities/{entity_id}", response_model=EntityResponseV2)
async def get_entity_by_id(
    project_id: ProjectExternalIdPathDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    entity_id: str = Path(..., description="Entity external ID (UUID)"),
) -> EntityResponseV2:
    """Get an entity by its external ID (UUID).

    This is the primary entity retrieval method in v2, using stable UUID
    identifiers that won't change with file moves.

    Args:
        entity_id: External ID (UUID string)

    Returns:
        Complete entity with observations and relations

    Raises:
        HTTPException: 404 if entity not found
    """
    logger.info(f"API v2 request: get_entity_by_id entity_id={entity_id}")

    entity = await entity_repository.get_by_external_id(entity_id)
    if not entity:
        raise HTTPException(
            status_code=404, detail=f"Entity with external_id '{entity_id}' not found"
        )

    result = EntityResponseV2.model_validate(entity)
    logger.info(f"API v2 response: external_id={entity_id}, title='{result.title}'")

    return result


## Create endpoints


@router.post("/entities", response_model=EntityResponseV2)
async def create_entity(
    project_id: ProjectExternalIdPathDep,
    data: Entity,
    background_tasks: BackgroundTasks,
    entity_service: EntityServiceV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
) -> EntityResponseV2:
    """Create a new entity.

    Args:
        data: Entity data to create

    Returns:
        Created entity with generated external_id (UUID)
    """
    logger.info(
        "API v2 request", endpoint="create_entity", entity_type=data.entity_type, title=data.title
    )

    entity = await entity_service.create_entity(data)

    # reindex
    await search_service.index_entity(entity, background_tasks=background_tasks)
    result = EntityResponseV2.model_validate(entity)

    logger.info(
        f"API v2 response: endpoint='create_entity' external_id={entity.external_id}, title={result.title}, permalink={result.permalink}, status_code=201"
    )
    return result


## Update endpoints


@router.put("/entities/{entity_id}", response_model=EntityResponseV2)
async def update_entity_by_id(
    data: Entity,
    response: Response,
    background_tasks: BackgroundTasks,
    project_id: ProjectExternalIdPathDep,
    entity_service: EntityServiceV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    sync_service: SyncServiceV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    entity_id: str = Path(..., description="Entity external ID (UUID)"),
) -> EntityResponseV2:
    """Update an entity by external ID.

    If the entity doesn't exist, it will be created (upsert behavior).

    Args:
        entity_id: External ID (UUID string)
        data: Updated entity data

    Returns:
        Updated entity
    """
    logger.info(f"API v2 request: update_entity_by_id entity_id={entity_id}")

    # Check if entity exists
    existing = await entity_repository.get_by_external_id(entity_id)
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
        f"API v2 response: external_id={entity_id}, created={created}, status_code={response.status_code}"
    )
    return result


@router.patch("/entities/{entity_id}", response_model=EntityResponseV2)
async def edit_entity_by_id(
    data: EditEntityRequest,
    background_tasks: BackgroundTasks,
    project_id: ProjectExternalIdPathDep,
    entity_service: EntityServiceV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    entity_id: str = Path(..., description="Entity external ID (UUID)"),
) -> EntityResponseV2:
    """Edit an existing entity by external ID using operations like append, prepend, etc.

    Args:
        entity_id: External ID (UUID string)
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
    entity = await entity_repository.get_by_external_id(entity_id)
    if not entity:
        raise HTTPException(
            status_code=404, detail=f"Entity with external_id '{entity_id}' not found"
        )

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
            f"API v2 response: external_id={entity_id}, operation='{data.operation}', status_code=200"
        )

        return result

    except Exception as e:
        logger.error(f"Error editing entity {entity_id}: {e}")
        raise HTTPException(status_code=400, detail=str(e))


## Delete endpoints


@router.delete("/entities/{entity_id}", response_model=DeleteEntitiesResponse)
async def delete_entity_by_id(
    background_tasks: BackgroundTasks,
    project_id: ProjectExternalIdPathDep,
    entity_service: EntityServiceV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    entity_id: str = Path(..., description="Entity external ID (UUID)"),
    search_service=Depends(lambda: None),  # Optional for now
) -> DeleteEntitiesResponse:
    """Delete an entity by external ID.

    Args:
        entity_id: External ID (UUID string)

    Returns:
        Deletion status

    Note: Returns deleted=False if entity doesn't exist (idempotent)
    """
    logger.info(f"API v2 request: delete_entity_by_id entity_id={entity_id}")

    entity = await entity_repository.get_by_external_id(entity_id)
    if entity is None:
        logger.info(f"API v2 response: external_id={entity_id} not found, deleted=False")
        return DeleteEntitiesResponse(deleted=False)

    # Delete the entity using internal ID
    deleted = await entity_service.delete_entity(entity.id)

    # Remove from search index if search service available
    if search_service:
        background_tasks.add_task(search_service.handle_delete, entity)

    logger.info(f"API v2 response: external_id={entity_id}, deleted={deleted}")

    return DeleteEntitiesResponse(deleted=deleted)


## Move endpoint


@router.put("/entities/{entity_id}/move", response_model=EntityResponseV2)
async def move_entity(
    data: MoveEntityRequestV2,
    background_tasks: BackgroundTasks,
    project_id: ProjectExternalIdPathDep,
    entity_service: EntityServiceV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    project_config: ProjectConfigV2ExternalDep,
    app_config: AppConfigDep,
    search_service: SearchServiceV2ExternalDep,
    entity_id: str = Path(..., description="Entity external ID (UUID)"),
) -> EntityResponseV2:
    """Move an entity to a new file location.

    V2 API uses external_id (UUID) in the URL path for stable references.
    The external_id will remain stable after the move.

    Args:
        project_id: Project external ID from URL path
        entity_id: Entity external ID from URL path (primary identifier)
        data: Move request with destination path only

    Returns:
        Updated entity with new file path
    """
    logger.info(
        f"API v2 request: move_entity entity_id={entity_id}, destination='{data.destination_path}'"
    )

    try:
        # First, get the entity by external_id to verify it exists
        entity = await entity_repository.get_by_external_id(entity_id)
        if not entity:
            raise HTTPException(
                status_code=404, detail=f"Entity with external_id '{entity_id}' not found"
            )

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
            f"API v2 response: moved external_id={entity_id} to '{data.destination_path}'"
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error moving entity: {e}")
        raise HTTPException(status_code=400, detail=str(e))
