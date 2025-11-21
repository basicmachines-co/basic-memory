"""V2 routes for getting entity content.

This router uses integer project IDs for stable, efficient routing.
V1 uses string-based project names which are less efficient and less stable.
"""

import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, BackgroundTasks, Body
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger

from basic_memory.deps import (
    ProjectConfigV2Dep,
    LinkResolverV2Dep,
    SearchServiceV2Dep,
    EntityServiceV2Dep,
    FileServiceV2Dep,
    EntityRepositoryV2Dep,
    ProjectIdPathDep,
)
from basic_memory.repository.search_repository import SearchIndexRow
from basic_memory.schemas.memory import normalize_memory_url
from basic_memory.schemas.search import SearchQuery, SearchItemType
from basic_memory.models.knowledge import Entity as EntityModel
from basic_memory.utils import validate_project_path
from datetime import datetime

# Note: No prefix here - it's added during registration as /v2/{project_id}/resource
router = APIRouter(tags=["resources"])


def get_entity_ids(item: SearchIndexRow) -> set[int]:
    """Extract entity IDs from a search result.

    Args:
        item: Search index row (entity, observation, or relation)

    Returns:
        Set of entity IDs related to this item
    """
    match item.type:
        case SearchItemType.ENTITY:
            return {item.id}
        case SearchItemType.OBSERVATION:
            return {item.entity_id}  # pyright: ignore [reportReturnType]
        case SearchItemType.RELATION:
            from_entity = item.from_id
            to_entity = item.to_id  # pyright: ignore [reportReturnType]
            return {from_entity, to_entity} if to_entity else {from_entity}  # pyright: ignore [reportReturnType]
        case _:  # pragma: no cover
            raise ValueError(f"Unexpected type: {item.type}")


@router.get("/resource/{identifier:path}")
async def get_resource_content(
    project_id: ProjectIdPathDep,
    config: ProjectConfigV2Dep,
    link_resolver: LinkResolverV2Dep,
    search_service: SearchServiceV2Dep,
    entity_service: EntityServiceV2Dep,
    file_service: FileServiceV2Dep,
    background_tasks: BackgroundTasks,
    identifier: str,
    page: int = 1,
    page_size: int = 10,
) -> FileResponse:
    """Get resource content by identifier.

    V2 supports both numeric entity IDs and legacy identifiers (permalinks).
    For best performance, use entity IDs directly: `/v2/{project_id}/resource/{entity_id}`

    Args:
        project_id: Validated numeric project ID from URL path
        config: Project configuration
        link_resolver: Link resolver for finding entities
        search_service: Search service for finding entities by permalink
        entity_service: Entity service for fetching entity data
        file_service: File service for reading file content
        background_tasks: FastAPI background tasks for cleanup
        identifier: Entity ID, permalink, or search pattern
        page: Page number for pagination (if multiple results)
        page_size: Number of results per page

    Returns:
        FileResponse with entity content (single file or concatenated markdown)
    """
    logger.debug(f"V2 Getting content for project {project_id}, identifier: {identifier}")

    # Get project path for validation
    project_path = Path(config.home)

    # Try numeric ID lookup first (V2 feature)
    entity = None
    if identifier.isdigit():
        entity_id = int(identifier)
        entities = await entity_service.get_entities_by_id([entity_id])
        entity = entities[0] if entities else None
        logger.debug(f"Numeric ID lookup: {'found' if entity else 'not found'}")

    # Fall back to link resolver for permalinks/paths
    if not entity:
        entity = await link_resolver.resolve_link(identifier)

    results = [entity] if entity else []

    # pagination for multiple results
    limit = page_size
    offset = (page - 1) * page_size

    # search using the identifier as a permalink
    if not results:
        # if the identifier contains a wildcard, use GLOB search
        query = (
            SearchQuery(permalink_match=identifier)
            if "*" in identifier
            else SearchQuery(permalink=identifier)
        )
        search_results = await search_service.search(query, limit, offset)
        if not search_results:
            raise HTTPException(status_code=404, detail=f"Resource not found: {identifier}")

        # get the deduplicated entities related to the search results
        entity_ids = {id for result in search_results for id in get_entity_ids(result)}
        results = await entity_service.get_entities_by_id(list(entity_ids))

    # return single response
    if len(results) == 1:
        entity = results[0]

        # Validate entity file path to prevent path traversal
        if not validate_project_path(entity.file_path, project_path):
            logger.error(
                f"Invalid file path in entity {entity.id}: {entity.file_path}"
            )
            raise HTTPException(
                status_code=500,
                detail="Entity contains invalid file path",
            )

        file_path = Path(f"{config.home}/{entity.file_path}")
        if not file_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"File not found: {file_path}",
            )
        return FileResponse(path=file_path)

    # for multiple files, initialize a temporary file for writing the results
    with tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".md") as tmp_file:
        temp_file_path = tmp_file.name

        for result in results:
            # Validate entity file path to prevent path traversal
            if not validate_project_path(result.file_path, project_path):
                logger.error(
                    f"Invalid file path in entity {result.id}: {result.file_path}"
                )
                continue  # Skip this entity and continue with others

            # Read content for each entity
            content = await file_service.read_entity_content(result)
            memory_url = normalize_memory_url(result.permalink)
            modified_date = result.updated_at.isoformat()
            checksum = result.checksum[:8] if result.checksum else ""

            # Prepare the delimited content
            response_content = f"--- {memory_url} {modified_date} {checksum}\n"
            response_content += f"\n{content}\n"
            response_content += "\n"

            # Write content directly to the temporary file in append mode
            tmp_file.write(response_content)

        # Ensure all content is written to disk
        tmp_file.flush()

    # Schedule the temporary file to be deleted after the response
    background_tasks.add_task(cleanup_temp_file, temp_file_path)

    # Return the file response
    return FileResponse(path=temp_file_path)


def cleanup_temp_file(file_path: str):
    """Delete the temporary file after response is sent.

    Args:
        file_path: Path to temporary file to delete
    """
    try:
        Path(file_path).unlink()  # Deletes the file
        logger.debug(f"Temporary file deleted: {file_path}")
    except Exception as e:  # pragma: no cover
        logger.error(f"Error deleting temporary file {file_path}: {e}")


@router.put("/resource/{file_path:path}")
async def write_resource(
    project_id: ProjectIdPathDep,
    config: ProjectConfigV2Dep,
    file_service: FileServiceV2Dep,
    entity_repository: EntityRepositoryV2Dep,
    search_service: SearchServiceV2Dep,
    file_path: str,
    content: Annotated[str, Body()],
) -> JSONResponse:
    """Write content to a file in the project.

    This endpoint allows writing content directly to a file in the project.
    Also creates an entity record and indexes the file for search.

    Args:
        project_id: Validated numeric project ID from URL path
        config: Project configuration
        file_service: File service for writing files
        entity_repository: Entity repository for creating/updating entities
        search_service: Search service for indexing
        file_path: Path to write to, relative to project root
        content: File content to write (raw string)

    Returns:
        JSON response with file information
    """
    try:
        # Defensive type checking: ensure content is a string
        # FastAPI should validate this, but if a dict somehow gets through
        # (e.g., via JSON body parsing), we need to catch it here
        if isinstance(content, dict):
            logger.error(
                f"Error writing resource {file_path}: "
                f"content is a dict, expected string. Keys: {list(content.keys())}"
            )
            raise HTTPException(
                status_code=400,
                detail="content must be a string, not a dict. "
                "Ensure request body is sent as raw string content, not JSON object.",
            )

        # Ensure it's UTF-8 string content
        if isinstance(content, bytes):  # pragma: no cover
            content_str = content.decode("utf-8")
        else:
            content_str = str(content)

        # Validate path to prevent path traversal attacks
        project_path = Path(config.home)
        if not validate_project_path(file_path, project_path):
            logger.warning(
                f"Invalid file path attempted: {file_path} in project {config.name}"
            )
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file path: {file_path}. "
                "Path must be relative and stay within project boundaries.",
            )

        # Get full file path
        full_path = Path(f"{config.home}/{file_path}")

        # Ensure parent directory exists
        full_path.parent.mkdir(parents=True, exist_ok=True)

        # Write content to file
        checksum = await file_service.write_file(full_path, content_str)

        # Get file info
        file_stats = file_service.file_stats(full_path)

        # Determine file details
        file_name = Path(file_path).name
        content_type = file_service.content_type(full_path)

        entity_type = "canvas" if file_path.endswith(".canvas") else "file"

        # Check if entity already exists
        existing_entity = await entity_repository.get_by_file_path(file_path)

        if existing_entity:
            # Update existing entity
            entity = await entity_repository.update(
                existing_entity.id,
                {
                    "title": file_name,
                    "entity_type": entity_type,
                    "content_type": content_type,
                    "file_path": file_path,
                    "checksum": checksum,
                    "updated_at": datetime.fromtimestamp(file_stats.st_mtime).astimezone(),
                },
            )
            status_code = 200
        else:
            # Create a new entity model
            entity = EntityModel(
                title=file_name,
                entity_type=entity_type,
                content_type=content_type,
                file_path=file_path,
                checksum=checksum,
                created_at=datetime.fromtimestamp(file_stats.st_ctime).astimezone(),
                updated_at=datetime.fromtimestamp(file_stats.st_mtime).astimezone(),
            )
            entity = await entity_repository.add(entity)
            status_code = 201

        # Index the file for search
        await search_service.index_entity(entity)  # pyright: ignore

        # Return success response
        return JSONResponse(
            status_code=status_code,
            content={
                "file_path": file_path,
                "checksum": checksum,
                "size": file_stats.st_size,
                "created_at": file_stats.st_ctime,
                "modified_at": file_stats.st_mtime,
            },
        )
    except HTTPException:
        # Re-raise HTTP exceptions (like validation errors) without wrapping
        raise
    except Exception as e:  # pragma: no cover
        logger.error(f"Error writing resource {file_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to write resource: {str(e)}")
