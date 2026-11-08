"""V2 Resource Router - ID-based resource content reads.

This router uses entity external_ids (UUIDs) for all operations, consistent with
v2's external_id-first design.

The resource surface is read-only by design: markdown notes are written through
the knowledge router's DB-first accepted-write pipeline, and every other file
kind (binaries, uploads, imports, external edits) arrives file-first through the
storage-event indexing pipeline. No API endpoint writes resource files inline.
"""

from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Request, Response, Path
from loguru import logger
from pydantic import BaseModel, ConfigDict

import logfire
from basic_memory import db
from basic_memory.deps import (
    ProjectConfigV2ExternalDep,
    FileServiceV2ExternalDep,
    EntityRepositoryV2ExternalDep,
    NoteContentQueryServiceDep,
    ReadCacheDep,
    SessionMakerDep,
)
from basic_memory.read_cache import ReadCacheOperation
from basic_memory.read_cache.policy import READ_CACHE_TTL_SECONDS
from basic_memory.read_cache.routes import ReadCacheRoute, cached_read, note_group
from basic_memory.utils import validate_project_path

router = APIRouter(prefix="/resource", tags=["resources-v2"], route_class=ReadCacheRoute)


class CachedResourceResponse(BaseModel):
    """Typed wire value for one resource response."""

    content: bytes
    media_type: str

    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")


@dataclass(frozen=True, slots=True)
class ResourceRead:
    """Cache a resource as its bytes and media type, and serve it with that media type.

    The SDK replays a stored body as JSON text, which would corrupt binary content
    and relabel markdown as application/json, so the entry is a typed envelope.
    """

    params: frozenset[str] = frozenset()

    def check(self, request: Request) -> None:
        return None

    def store(self, response: Response) -> bytes:
        if response.media_type is None:  # pragma: no cover
            raise TypeError("resource responses always carry a media type")
        return (
            CachedResourceResponse(content=bytes(response.body), media_type=response.media_type)
            .model_dump_json()
            .encode()
        )

    def render(self, stored: bytes, request: Request) -> Response:
        return _content_response(CachedResourceResponse.model_validate_json(stored), None)


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


# A note's resource is its accepted markdown, and a file's is its stored bytes; both
# change only through writes and index passes that retire the note's own group.
# Ranged reads bypass the cache (see read_cache.routes._cached_target).
@router.get(
    "/{entity_id}",
    dependencies=[
        cached_read(
            ReadCacheOperation.resource,
            group=note_group,
            ttl_seconds=READ_CACHE_TTL_SECONDS,
            rendered=ResourceRead(),
        )
    ],
)
async def get_resource_content(
    config: ProjectConfigV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
    note_content_query_service: NoteContentQueryServiceDep,
    read_cache: ReadCacheDep,
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
            if not validate_project_path(entity_file_path, file_service.local_project_path):
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
        return _content_response(resource, range_header)
