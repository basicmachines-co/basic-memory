"""V2 Resource Router - ID-based resource content reads.

This router uses entity external_ids (UUIDs) for all operations, consistent with
v2's external_id-first design.

The resource surface is read-only by design: markdown notes are written through
the knowledge router's DB-first accepted-write pipeline, and every other file
kind (binaries, uploads, imports, external edits) arrives file-first through the
storage-event indexing pipeline. No API endpoint writes resource files inline.
"""

from contextlib import nullcontext
from pathlib import Path as PathLib
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, Path
from loguru import logger
from pydantic import BaseModel, ConfigDict

import logfire
from basic_memory import db
from basic_memory.deps import (
    create_model_read_cache,
    ProjectConfigV2ExternalDep,
    FileServiceV2ExternalDep,
    EntityRepositoryV2ExternalDep,
    NoteContentQueryServiceDep,
    ReadCacheDep,
    SessionMakerDep,
)
from basic_memory.read_cache import (
    ModelReadCache,
    ReadCacheKey,
    ReadCacheOperation,
    ReadCacheScope,
    read_cache_request_digest,
)
from basic_memory.utils import validate_project_path

router = APIRouter(prefix="/resource", tags=["resources-v2"])


class CachedResourceResponse(BaseModel):
    """Typed wire value for one cacheable resource response."""

    content: bytes
    media_type: str

    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")


def get_resource_read_cache(
    read_cache: ReadCacheDep,
) -> ModelReadCache[CachedResourceResponse] | None:
    """Bind resource responses to the optional cache backend."""
    return create_model_read_cache(read_cache, CachedResourceResponse)


ResourceReadCacheDep = Annotated[
    ModelReadCache[CachedResourceResponse] | None,
    Depends(get_resource_read_cache),
]


def _is_markdown_resource(resource: CachedResourceResponse) -> bool:
    return resource.media_type.partition(";")[0].strip().lower() == "text/markdown"


@router.get("/{entity_id}")
async def get_resource_content(
    config: ProjectConfigV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
    note_content_query_service: NoteContentQueryServiceDep,
    read_cache: ResourceReadCacheDep,
    session_maker: SessionMakerDep,
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
    with logfire.span(
        "api.request.resource.get_content",
        entrypoint="api",
        domain="resource",
        action="get_content",
    ):
        logger.debug(f"V2 Getting content for project {project_id}, entity_id: {entity_id}")

        cache_key = ReadCacheKey(
            project_id=project_id,
            operation=ReadCacheOperation.resource,
            request_digest=read_cache_request_digest(entity_id),
        )
        cache_scope = (
            read_cache.read(key=cache_key)
            if read_cache is not None
            else nullcontext(ReadCacheScope[CachedResourceResponse]())
        )
        async with cache_scope as cached:
            if cached.value is not None:
                return Response(
                    content=cached.value.content,
                    media_type=cached.value.media_type,
                )

            # Keep the DB session open only for the lookups; close it before the
            # filesystem I/O below so large/slow resource reads don't pin a pooled
            # connection (and an open read transaction on Postgres) for their duration.
            async with db.scoped_session(session_maker) as session:
                note_resource = await note_content_query_service.get_note_resource_with_read_repair(
                    project_external_id=project_id,
                    entity_external_id=entity_id,
                    session=session,
                    read_cache=read_cache,
                )
                if note_resource is not None:
                    resource = CachedResourceResponse(
                        content=note_resource.content.encode("utf-8"),
                        media_type=note_resource.content_type,
                    )
                    cached.value = resource
                    return Response(
                        content=resource.content,
                        media_type=resource.media_type,
                    )

                with logfire.span(
                    "api.resource.get_content.load_entity",
                    domain="resource",
                    action="get_content",
                    phase="load_entity",
                ):
                    entity = await entity_repository.get_by_external_id(session, entity_id)
                if not entity:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Entity {entity_id} not found",
                    )
                # Copy the scalar columns needed for file I/O so the session can close.
                entity_file_path = entity.file_path
                entity_db_id = entity.id

            with logfire.span(
                "api.resource.get_content.validate_path",
                domain="resource",
                action="get_content",
                phase="validate_path",
            ):
                project_path = PathLib(config.home)
                if not validate_project_path(entity_file_path, project_path):
                    logger.error(  # pragma: no cover
                        f"Invalid file path in entity {entity_db_id}: {entity_file_path}"
                    )
                    raise HTTPException(  # pragma: no cover
                        status_code=500,
                        detail="Entity contains invalid file path",
                    )

            with logfire.span(
                "api.resource.get_content.ensure_exists",
                domain="resource",
                action="get_content",
                phase="ensure_exists",
            ):
                if not await file_service.exists(entity_file_path):
                    raise HTTPException(  # pragma: no cover
                        status_code=404,
                        detail=f"File not found: {entity_file_path}",
                    )

            with logfire.span(
                "api.resource.get_content.read_content",
                domain="resource",
                action="get_content",
                phase="read_content",
            ):
                content = await file_service.read_file_bytes(entity_file_path)
                content_type = file_service.content_type(entity_file_path)

            resource = CachedResourceResponse(
                content=content,
                media_type=content_type,
            )
            cached.cacheable = _is_markdown_resource(resource)
            cached.value = resource
            return Response(
                content=resource.content,
                media_type=resource.media_type,
            )
