"""V2 Resource Router - ID-based resource content operations.

This router uses entity external_ids (UUIDs) for all operations, with file paths
in request bodies when needed. This is consistent with v2's external_id-first design.

Key differences from v1:
- Uses UUID external_ids in URL paths instead of integer IDs or file paths
- File paths are in request bodies for create/update operations
- More RESTful: POST for create, PUT for update, GET for read
"""

import uuid
from pathlib import Path as PathLib

from fastapi import APIRouter, HTTPException, Response, Path
from loguru import logger

from basic_memory import telemetry
from basic_memory.deps import (
    ProjectConfigV2ExternalDep,
    FileServiceV2ExternalDep,
    EntityRepositoryV2ExternalDep,
    SearchServiceV2ExternalDep,
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
    config: ProjectConfigV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
    project_id: str = Path(..., description="Project external UUID"),
    entity_id: str = Path(..., description="Entity external UUID"),
) -> Response:
    """Get raw resource content by entity external_id.

    Args:
        project_id: Project external UUID from URL path
        entity_id: Entity external UUID
        config: Project configuration
        entity_repository: Entity repository for fetching entity data
        file_service: File service for reading file content

    Returns:
        Response with entity content

    Raises:
        HTTPException: 404 if entity or file not found
    """
    with telemetry.operation(
        "api.request.resource.get_content",
        entrypoint="api",
        domain="resource",
        action="get_content",
    ):
        logger.debug(f"V2 Getting content for project {project_id}, entity_id: {entity_id}")

        with telemetry.scope(
            "api.resource.get_content.load_entity",
            domain="resource",
            action="get_content",
            phase="load_entity",
        ):
            entity = await entity_repository.get_by_external_id(entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

        with telemetry.scope(
            "api.resource.get_content.validate_path",
            domain="resource",
            action="get_content",
            phase="validate_path",
        ):
            project_path = PathLib(config.home)
            if not validate_project_path(entity.file_path, project_path):
                logger.error(  # pragma: no cover
                    f"Invalid file path in entity {entity.id}: {entity.file_path}"
                )
                raise HTTPException(  # pragma: no cover
                    status_code=500,
                    detail="Entity contains invalid file path",
                )

        with telemetry.scope(
            "api.resource.get_content.ensure_exists",
            domain="resource",
            action="get_content",
            phase="ensure_exists",
        ):
            if not await file_service.exists(entity.file_path):
                raise HTTPException(  # pragma: no cover
                    status_code=404,
                    detail=f"File not found: {entity.file_path}",
                )

        with telemetry.scope(
            "api.resource.get_content.read_content",
            domain="resource",
            action="get_content",
            phase="read_content",
        ):
            content = await file_service.read_file_bytes(entity.file_path)
            content_type = file_service.content_type(entity.file_path)

        return Response(content=content, media_type=content_type)


@router.post("", response_model=ResourceResponse)
async def create_resource(
    data: CreateResourceRequest,
    config: ProjectConfigV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    project_id: str = Path(..., description="Project external UUID"),
) -> ResourceResponse:
    """Create a new resource file.

    Args:
        project_id: Project external UUID from URL path
        data: Create resource request with file_path and content
        config: Project configuration
        file_service: File service for writing files
        entity_repository: Entity repository for creating entities
        search_service: Search service for indexing

    Returns:
        ResourceResponse with file information including entity_id and external_id

    Raises:
        HTTPException: 400 for invalid file paths, 409 if file already exists
    """
    with telemetry.operation(
        "api.request.resource.create",
        entrypoint="api",
        domain="resource",
        action="create",
    ):
        try:
            # Validate path to prevent path traversal attacks
            project_path = PathLib(config.home)
            if not validate_project_path(data.file_path, project_path):
                logger.warning(
                    f"Invalid file path attempted: {data.file_path} in project {config.name}"
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid file path: {data.file_path}. "
                    "Path must be relative and stay within project boundaries.",
                )

            existing_entity = await entity_repository.get_by_file_path(data.file_path)
            if existing_entity:
                raise HTTPException(
                    status_code=409,
                    detail=f"Resource already exists at {data.file_path} with entity_id {existing_entity.external_id}. "
                    f"Use PUT /resource/{existing_entity.external_id} to update it.",
                )

            with telemetry.scope(
                "api.resource.create.write_file",
                domain="resource",
                action="create",
                phase="write_file",
            ):
                await file_service.ensure_directory(PathLib(data.file_path).parent)
                checksum = await file_service.write_file(data.file_path, data.content)

            with telemetry.scope(
                "api.resource.create.read_metadata",
                domain="resource",
                action="create",
                phase="read_metadata",
            ):
                file_metadata = await file_service.get_file_metadata(data.file_path)

            file_name = PathLib(data.file_path).name
            content_type = file_service.content_type(data.file_path)
            note_type = "canvas" if data.file_path.endswith(".canvas") else "file"

            entity = EntityModel(
                external_id=str(uuid.uuid4()),
                title=file_name,
                note_type=note_type,
                content_type=content_type,
                file_path=data.file_path,
                checksum=checksum,
                created_at=file_metadata.created_at,
                updated_at=file_metadata.modified_at,
            )
            with telemetry.scope(
                "api.resource.create.upsert_entity",
                domain="resource",
                action="create",
                phase="upsert_entity",
            ):
                entity = await entity_repository.add(entity)

            with telemetry.scope(
                "api.resource.create.search_index",
                domain="resource",
                action="create",
                phase="search_index",
            ):
                await search_service.index_entity(entity)

            return ResourceResponse(
                entity_id=entity.id,
                external_id=entity.external_id,
                file_path=data.file_path,
                checksum=checksum,
                size=file_metadata.size,
                created_at=file_metadata.created_at.timestamp(),
                modified_at=file_metadata.modified_at.timestamp(),
            )
        except HTTPException:
            raise
        except Exception as e:  # pragma: no cover
            logger.error(f"Error creating resource {data.file_path}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to create resource: {str(e)}")


@router.put("/{entity_id}", response_model=ResourceResponse)
async def update_resource(
    data: UpdateResourceRequest,
    config: ProjectConfigV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    project_id: str = Path(..., description="Project external UUID"),
    entity_id: str = Path(..., description="Entity external UUID"),
) -> ResourceResponse:
    """Update an existing resource by entity external_id.

    Can update content and optionally move the file to a new path.

    Args:
        project_id: Project external UUID from URL path
        entity_id: Entity external UUID of the resource to update
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
    with telemetry.operation(
        "api.request.resource.update",
        entrypoint="api",
        domain="resource",
        action="update",
    ):
        try:
            entity = await entity_repository.get_by_external_id(entity_id)
            if not entity:
                raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

            target_file_path = data.file_path if data.file_path else entity.file_path

            project_path = PathLib(config.home)
            if not validate_project_path(target_file_path, project_path):
                logger.warning(
                    f"Invalid file path attempted: {target_file_path} in project {config.name}"
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid file path: {target_file_path}. "
                    "Path must be relative and stay within project boundaries.",
                )

            with telemetry.scope(
                "api.resource.update.write_file",
                domain="resource",
                action="update",
                phase="write_file",
            ):
                if data.file_path and data.file_path != entity.file_path:
                    await file_service.ensure_directory(PathLib(target_file_path).parent)
                    if await file_service.exists(entity.file_path):
                        await file_service.delete_file(entity.file_path)
                else:
                    await file_service.ensure_directory(PathLib(target_file_path).parent)

                checksum = await file_service.write_file(target_file_path, data.content)

            with telemetry.scope(
                "api.resource.update.read_metadata",
                domain="resource",
                action="update",
                phase="read_metadata",
            ):
                file_metadata = await file_service.get_file_metadata(target_file_path)

            file_name = PathLib(target_file_path).name
            content_type = file_service.content_type(target_file_path)
            note_type = "canvas" if target_file_path.endswith(".canvas") else "file"

            with telemetry.scope(
                "api.resource.update.update_entity",
                domain="resource",
                action="update",
                phase="update_entity",
            ):
                updated_entity = await entity_repository.update(
                    entity.id,
                    {
                        "title": file_name,
                        "note_type": note_type,
                        "content_type": content_type,
                        "file_path": target_file_path,
                        "checksum": checksum,
                        "updated_at": file_metadata.modified_at,
                    },
                )
            if updated_entity is None:
                raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

            with telemetry.scope(
                "api.resource.update.search_index",
                domain="resource",
                action="update",
                phase="search_index",
            ):
                await search_service.index_entity(updated_entity)

            return ResourceResponse(
                entity_id=entity.id,
                external_id=entity.external_id,
                file_path=target_file_path,
                checksum=checksum,
                size=file_metadata.size,
                created_at=file_metadata.created_at.timestamp(),
                modified_at=file_metadata.modified_at.timestamp(),
            )
        except HTTPException:
            raise
        except Exception as e:  # pragma: no cover
            logger.error(f"Error updating resource {entity_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to update resource: {str(e)}")
