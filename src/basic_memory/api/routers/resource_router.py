"""Routes for getting entity content."""

import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from loguru import logger

from basic_memory.deps import (
    ProjectConfigDep,
    LinkResolverDep,
    SearchServiceDep,
    EntityServiceDep,
    FileServiceDep,
)
from basic_memory.repository.search_repository import SearchIndexRow
from basic_memory.schemas.memory import normalize_memory_url
from basic_memory.schemas.search import SearchQuery, SearchItemType

router = APIRouter(prefix="/resource", tags=["resources"])


def get_entity_ids(item: SearchIndexRow) -> list[int]:
    match item.type:
        case SearchItemType.ENTITY:
            return [item.id]
        case SearchItemType.OBSERVATION:
            return [item.entity_id]  # pyright: ignore [reportReturnType]
        case SearchItemType.RELATION:
            from_entity = item.from_id
            to_entity = item.to_id  # pyright: ignore [reportReturnType]
            return [from_entity, to_entity] if to_entity else [from_entity]  # pyright: ignore [reportReturnType]
        case _:  # pragma: no cover
            raise ValueError(f"Unexpected type: {item.type}")


@router.get("/{identifier:path}")
async def get_resource_content(
    config: ProjectConfigDep,
    link_resolver: LinkResolverDep,
    search_service: SearchServiceDep,
    entity_service: EntityServiceDep,
    file_service: FileServiceDep,
    background_tasks: BackgroundTasks,
    identifier: str,
) -> FileResponse:
    """Get resource content by identifier: name or permalink."""
    logger.debug(f"Getting content for: {identifier}")

    # Find single entity by permalink
    entity = await link_resolver.resolve_link(identifier)
    results = [entity] if entity else []

    # search using the identifier as a permalink
    if not results:
        # if the identifier contains a wildcard, use GLOB search
        query = (
            SearchQuery(permalink_match=identifier)
            if "*" in identifier
            else SearchQuery(permalink=identifier)
        )
        search_results = await search_service.search(query)
        if not search_results:
            raise HTTPException(status_code=404, detail=f"Resource not found: {identifier}")

        # get the entities related to the search results
        entity_ids = [id for result in search_results for id in get_entity_ids(result)]
        results = await entity_service.get_entities_by_id(entity_ids)

    # return single response
    if len(results) == 1:
        entity = results[0]
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
            # Read content for each entity
            content = await file_service.read_entity_content(result)
            memory_url = normalize_memory_url(result.permalink)
            modified_date = result.updated_at.isoformat()
            assert result.checksum
            checksum = result.checksum[:8]

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
