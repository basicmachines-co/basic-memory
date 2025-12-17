"""V2 Resource Router - ID-based resource content operations.

This router uses entity IDs for all operations, with file paths in request bodies
when needed. This is consistent with v2's ID-first design.

Key differences from v1:
- Uses integer entity IDs in URL paths instead of file paths
- File paths are in request bodies for create/update operations
- More RESTful: POST for create, PUT for update, GET for read
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from loguru import logger

from basic_memory.deps import (
    ProjectConfigV2Dep,
    EntityServiceV2Dep,
    FileServiceV2Dep,
    EntityRepositoryV2Dep,
    SearchServiceV2Dep,
    ProjectIdPathDep,
)
from basic_memory.models.knowledge import Entity as EntityModel
from basic_memory.schemas.v2.resource import (
    CreateResourceRequest,
    UpdateResourceRequest,
    ResourceResponse,
)
from basic_memory.utils import validate_project_path

router = APIRouter(prefix="/resource", tags=["resources-v2"])


@router.get("/{entity_id}")
async def get_resource_content(
    project_id: ProjectIdPathDep,
    entity_id: int,
    config: ProjectConfigV2Dep,
    entity_service: EntityServiceV2Dep,
    file_service: FileServiceV2Dep,
) -> Response:
    """Get raw resource content by entity ID.

    Args:
        project_id: Validated numeric project ID from URL path
        entity_id: Numeric entity ID
        config: Project configuration
        entity_service: Entity service for fetching entity data
        file_service: File service for reading file content

    Returns:
        Response with entity content

    Raises:
        HTTPException: 404 if entity or file not found
    """
    logger.debug(f"V2 Getting content for project {project_id}, entity_id: {entity_id}")

    # Get entity by ID
    entities = await entity_service.get_entities_by_id([entity_id])
    if not entities:
        raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

    entity = entities[0]

    # Validate entity file path to prevent path traversal
    project_path = Path(config.home)
    if not validate_project_path(entity.file_path, project_path):
        logger.error(f"Invalid file path in entity {entity.id}: {entity.file_path}")
        raise HTTPException(
            status_code=500,
            detail="Entity contains invalid file path",
        )

    # Check file exists via file_service (for cloud compatibility)
    if not await file_service.exists(entity.file_path):
        raise HTTPException(
            status_code=404,
            detail=f"File not found: {entity.file_path}",
        )

    # Read content via file_service as bytes (works with both local and S3)
    content = await file_service.read_file_bytes(entity.file_path)
    content_type = file_service.content_type(entity.file_path)

    return Response(content=content, media_type=content_type)


@router.post("", response_model=ResourceResponse)
async def create_resource(
    project_id: ProjectIdPathDep,
    data: CreateResourceRequest,
    config: ProjectConfigV2Dep,
    file_service: FileServiceV2Dep,
    entity_repository: EntityRepositoryV2Dep,
    search_service: SearchServiceV2Dep,
) -> ResourceResponse:
    """Create a new resource file.

    Args:
        project_id: Validated numeric project ID from URL path
        data: Create resource request with file_path and content
        config: Project configuration
        file_service: File service for writing files
        entity_repository: Entity repository for creating entities
        search_service: Search service for indexing

    Returns:
        ResourceResponse with file information including entity_id

    Raises:
        HTTPException: 400 for invalid file paths, 409 if file already exists
    """
    try:
        # Validate path to prevent path traversal attacks
        project_path = Path(config.home)
        if not validate_project_path(data.file_path, project_path):
            logger.warning(
                f"Invalid file path attempted: {data.file_path} in project {config.name}"
            )
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file path: {data.file_path}. "
                "Path must be relative and stay within project boundaries.",
            )

        # Check if entity already exists
        existing_entity = await entity_repository.get_by_file_path(data.file_path)
        if existing_entity:
            raise HTTPException(
                status_code=409,
                detail=f"Resource already exists at {data.file_path} with entity_id {existing_entity.id}. "
                f"Use PUT /resource/{existing_entity.id} to update it.",
            )

        # Cloud compatibility: avoid assuming a local filesystem path.
        # Delegate directory creation + writes to FileService (local or S3).
        await file_service.ensure_directory(Path(data.file_path).parent)
        checksum = await file_service.write_file(data.file_path, data.content)

        # Get file info
        file_metadata = await file_service.get_file_metadata(data.file_path)

        # Determine file details
        file_name = Path(data.file_path).name
        content_type = file_service.content_type(data.file_path)
        entity_type = "canvas" if data.file_path.endswith(".canvas") else "file"

        # Create a new entity model
        entity = EntityModel(
            title=file_name,
            entity_type=entity_type,
            content_type=content_type,
            file_path=data.file_path,
            checksum=checksum,
            created_at=file_metadata.created_at,
            updated_at=file_metadata.modified_at,
        )
        entity = await entity_repository.add(entity)

        # Index the file for search
        await search_service.index_entity(entity)  # pyright: ignore

        # Return success response
        return ResourceResponse(
            entity_id=entity.id,
            file_path=data.file_path,
            checksum=checksum,
            size=file_metadata.size,
            created_at=file_metadata.created_at.timestamp(),
            modified_at=file_metadata.modified_at.timestamp(),
        )
    except HTTPException:
        # Re-raise HTTP exceptions without wrapping
        raise
    except Exception as e:  # pragma: no cover
        logger.error(f"Error creating resource {data.file_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create resource: {str(e)}")


@router.put("/{entity_id}", response_model=ResourceResponse)
async def update_resource(
    project_id: ProjectIdPathDep,
    entity_id: int,
    data: UpdateResourceRequest,
    config: ProjectConfigV2Dep,
    file_service: FileServiceV2Dep,
    entity_repository: EntityRepositoryV2Dep,
    search_service: SearchServiceV2Dep,
) -> ResourceResponse:
    """Update an existing resource by entity ID.

    Can update content and optionally move the file to a new path.

    Args:
        project_id: Validated numeric project ID from URL path
        entity_id: Entity ID of the resource to update
        data: Update resource request with content and optional new file_path
        config: Project configuration
        file_service: File service for writing files
        entity_repository: Entity repository for updating entities
        search_service: Search service for indexing

    Returns:
        ResourceResponse with updated file information

    Raises:
        HTTPException: 404 if entity not found, 400 for invalid paths
    """
    try:
        # Get existing entity
        entity = await entity_repository.get_by_id(entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

        # Determine target file path
        target_file_path = data.file_path if data.file_path else entity.file_path

        # Validate path to prevent path traversal attacks
        project_path = Path(config.home)
        if not validate_project_path(target_file_path, project_path):
            logger.warning(
                f"Invalid file path attempted: {target_file_path} in project {config.name}"
            )
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file path: {target_file_path}. "
                "Path must be relative and stay within project boundaries.",
            )

        # If moving file, handle the move
        if data.file_path and data.file_path != entity.file_path:
            # Ensure new parent directory exists (no-op for S3)
            await file_service.ensure_directory(Path(target_file_path).parent)

            # If old file exists, remove it via file_service (for cloud compatibility)
            if await file_service.exists(entity.file_path):
                await file_service.delete_file(entity.file_path)
        else:
            # Ensure directory exists for in-place update
            await file_service.ensure_directory(Path(target_file_path).parent)

        # Write content to target file
        checksum = await file_service.write_file(target_file_path, data.content)

        # Get file info
        file_metadata = await file_service.get_file_metadata(target_file_path)

        # Determine file details
        file_name = Path(target_file_path).name
        content_type = file_service.content_type(target_file_path)
        entity_type = "canvas" if target_file_path.endswith(".canvas") else "file"

        # Update entity
        updated_entity = await entity_repository.update(
            entity_id,
            {
                "title": file_name,
                "entity_type": entity_type,
                "content_type": content_type,
                "file_path": target_file_path,
                "checksum": checksum,
                "updated_at": file_metadata.modified_at,
            },
        )

        # Index the updated file for search
        await search_service.index_entity(updated_entity)  # pyright: ignore

        # Return success response
        return ResourceResponse(
            entity_id=entity_id,
            file_path=target_file_path,
            checksum=checksum,
            size=file_metadata.size,
            created_at=file_metadata.created_at.timestamp(),
            modified_at=file_metadata.modified_at.timestamp(),
        )
    except HTTPException:
        # Re-raise HTTP exceptions without wrapping
        raise
    except Exception as e:  # pragma: no cover
        logger.error(f"Error updating resource {entity_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update resource: {str(e)}")
