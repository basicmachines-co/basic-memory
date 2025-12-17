"""Routes for getting entity content."""

import tempfile
from pathlib import Path
from typing import Annotated, Union

from fastapi import APIRouter, HTTPException, BackgroundTasks, Body, Response
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger

from basic_memory.deps import (
    ProjectConfigDep,
    LinkResolverDep,
    SearchServiceDep,
    EntityServiceDep,
    FileServiceDep,
    EntityRepositoryDep,
)
from basic_memory.repository.search_repository import SearchIndexRow
from basic_memory.schemas.memory import normalize_memory_url
from basic_memory.schemas.search import SearchQuery, SearchItemType
from basic_memory.models.knowledge import Entity as EntityModel
from datetime import datetime

router = APIRouter(prefix="/resource", tags=["resources"])


def _mtime_to_datetime(entity: EntityModel) -> datetime:
    """Convert entity mtime (file modification time) to datetime.

    Returns the file's actual modification time, falling back to updated_at
    if mtime is not available.
    """
    if entity.mtime:
        return datetime.fromtimestamp(entity.mtime).astimezone()
    return entity.updated_at


def get_entity_ids(item: SearchIndexRow) -> set[int]:
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


@router.get("/{identifier:path}", response_model=None)
async def get_resource_content(
    config: ProjectConfigDep,
    link_resolver: LinkResolverDep,
    search_service: SearchServiceDep,
    entity_service: EntityServiceDep,
    file_service: FileServiceDep,
    background_tasks: BackgroundTasks,
    identifier: str,
    page: int = 1,
    page_size: int = 10,
) -> Union[Response, FileResponse]:
    """Get resource content by identifier: name or permalink."""
    logger.debug(f"Getting content for: {identifier}")

    # Find single entity by permalink
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

    # for multiple files, initialize a temporary file for writing the results
    with tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".md") as tmp_file:
        temp_file_path = tmp_file.name

        for result in results:
            # Read content for each entity
            content = await file_service.read_entity_content(result)
            memory_url = normalize_memory_url(result.permalink)
            modified_date = _mtime_to_datetime(result).isoformat()
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
    """Delete the temporary file."""
    try:
        Path(file_path).unlink()  # Deletes the file
        logger.debug(f"Temporary file deleted: {file_path}")
    except Exception as e:  # pragma: no cover
        logger.error(f"Error deleting temporary file {file_path}: {e}")


@router.put("/{file_path:path}")
async def write_resource(
    config: ProjectConfigDep,
    file_service: FileServiceDep,
    entity_repository: EntityRepositoryDep,
    search_service: SearchServiceDep,
    file_path: str,
    content: Annotated[str, Body()],
) -> JSONResponse:
    """Write content to a file in the project.

    This endpoint allows writing content directly to a file in the project.
    Also creates an entity record and indexes the file for search.

    Args:
        file_path: Path to write to, relative to project root
        request: Contains the content to write

    Returns:
        JSON response with file information
    """
    try:
        # Get content from request body

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

        # Cloud compatibility: do not assume a local filesystem path structure.
        # Delegate directory creation + writes to the configured FileService (local or S3).
        await file_service.ensure_directory(Path(file_path).parent)
        checksum = await file_service.write_file(file_path, content_str)

        # Get file info
        file_metadata = await file_service.get_file_metadata(file_path)

        # Determine file details
        file_name = Path(file_path).name
        content_type = file_service.content_type(file_path)

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
                    "updated_at": file_metadata.modified_at,
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
                created_at=file_metadata.created_at,
                updated_at=file_metadata.modified_at,
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
                "size": file_metadata.size,
                "created_at": file_metadata.created_at.timestamp(),
                "modified_at": file_metadata.modified_at.timestamp(),
            },
        )
    except Exception as e:  # pragma: no cover
        logger.error(f"Error writing resource {file_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to write resource: {str(e)}")
