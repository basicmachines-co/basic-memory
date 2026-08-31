"""V2 Resource Router - ID-based resource content reads.

This router uses entity external_ids (UUIDs) for all operations, consistent with
v2's external_id-first design.

The resource surface is read-only by design: markdown notes are written through
the knowledge router's DB-first accepted-write pipeline, and every other file
kind (binaries, uploads, imports, external edits) arrives file-first through the
storage-event indexing pipeline. No API endpoint writes resource files inline.
"""

from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path as PathLib
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response, Path
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


# --- HTTP Range support (RFC 9110, single bytes range) ---


@dataclass(frozen=True, slots=True)
class _SatisfiableByteRange:
    """One satisfiable byte span, both bounds inclusive."""

    start: int
    end: int


def _parse_byte_range(
    header: str, total_bytes: int
) -> _SatisfiableByteRange | Literal["ignored", "unsatisfiable"]:
    """Parse a Range header against a body of ``total_bytes``.

    Only the ``bytes`` unit and a single range are supported; anything else —
    other units, multi-range, malformed specs — is "ignored" per RFC 9110 and
    the full body is served with 200. A syntactically valid range that lies
    entirely past the end of the body (or a zero-length suffix) is
    "unsatisfiable" and maps to 416.
    """
    unit, separator, spec = header.partition("=")
    if not separator or unit.strip().lower() != "bytes":
        return "ignored"
    spec = spec.strip()
    if "," in spec:
        return "ignored"
    first, dash, last = spec.partition("-")
    first = first.strip()
    last = last.strip()
    if not dash:
        return "ignored"
    if first:
        if not first.isdigit() or (last and not last.isdigit()):
            return "ignored"
        start = int(first)
        end = int(last) if last else total_bytes - 1
        if last and end < start:
            return "ignored"
        if start >= total_bytes:
            return "unsatisfiable"
        return _SatisfiableByteRange(start=start, end=min(end, total_bytes - 1))
    # Suffix form "bytes=-n": the final n bytes of the body.
    if not last or not last.isdigit():
        return "ignored"
    suffix_length = int(last)
    if suffix_length == 0 or total_bytes == 0:
        return "unsatisfiable"
    return _SatisfiableByteRange(start=max(0, total_bytes - suffix_length), end=total_bytes - 1)


def _content_response(resource: CachedResourceResponse, range_header: str | None) -> Response:
    """Serve one fully buffered resource, honoring a single-range Range header.

    The cache always stores the full bytes; ranges are sliced per request after
    retrieval, so the Range header is deliberately NOT part of the cache digest.
    ``If-Range`` is not supported and is ignored.
    """
    total_bytes = len(resource.content)
    if range_header is not None:
        match _parse_byte_range(range_header, total_bytes):
            case "unsatisfiable":
                return Response(
                    status_code=416,
                    headers={
                        "content-range": f"bytes */{total_bytes}",
                        "accept-ranges": "bytes",
                    },
                )
            case _SatisfiableByteRange(start=start, end=end):
                return Response(
                    status_code=206,
                    content=resource.content[start : end + 1],
                    media_type=resource.media_type,
                    headers={
                        "content-range": f"bytes {start}-{end}/{total_bytes}",
                        "accept-ranges": "bytes",
                    },
                )
            case "ignored":
                pass
    return Response(
        content=resource.content,
        media_type=resource.media_type,
        headers={"accept-ranges": "bytes"},
    )


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
    range_header: Annotated[
        str | None,
        Header(
            alias="Range",
            description="Optional single bytes range, e.g. 'bytes=0-1023'; "
            "served as 206 Partial Content",
        ),
    ] = None,
) -> Response:
    """Get raw resource content by entity external_id.

    Supports single-range HTTP Range requests (``bytes=a-b``, ``bytes=a-``,
    ``bytes=-n``) with 206/416 responses; multi-range and non-bytes units are
    ignored and the full body is served. ``If-Range`` is not supported.

    Args:
        project_id: Project external UUID from URL path
        entity_id: Entity external UUID
        config: Project configuration
        entity_repository: Entity repository for fetching entity data
        file_service: File service for reading file content
        range_header: Optional HTTP Range header for partial content

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
                return _content_response(cached.value, range_header)

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
                    return _content_response(resource, range_header)

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
            return _content_response(resource, range_header)
