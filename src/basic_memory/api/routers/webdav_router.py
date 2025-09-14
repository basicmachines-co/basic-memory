"""WebDAV router for basic-memory project uploads."""

import os
import shutil
from datetime import datetime
from pathlib import Path
import aiofiles
from loguru import logger

from basic_memory.deps import ProjectPathDep, ProjectServiceDep
from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import StreamingResponse

router = APIRouter(
    prefix="/webdav",
    tags=["webdav"],
)


async def get_project_path_or_404(project_service: ProjectServiceDep, project: str) -> Path:
    found_project = await project_service.get_project(project)
    if not found_project:
        raise HTTPException(status_code=404, detail=f"Project: '{project}' does not exist")
    return Path(found_project.path)


async def get_project_file_path_or_404(project_path: Path, path: str) -> Path:
    file_path = Path(project_path / path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File: '{path}' does not exist")
    return file_path


@router.api_route("/{path:path}", methods=["OPTIONS"])
async def webdav_options(
    path: str,
    project: ProjectPathDep,
    project_service: ProjectServiceDep,
):
    """WebDAV OPTIONS endpoint."""
    project_path = await get_project_path_or_404(project_service, project)
    file_path = await get_project_file_path_or_404(project_path, path)
    return await _webdav_options(file_path)


@router.api_route("/{path:path}", methods=["PROPFIND"])
async def webdav_propfind(
    path: str,
    project: ProjectPathDep,
    project_service: ProjectServiceDep,
):
    """WebDAV PROPFIND endpoint."""
    project_path = await get_project_path_or_404(project_service, project)
    file_path = await get_project_file_path_or_404(project_path, path)
    return await _webdav_propfind(project, project_path, file_path)


@router.api_route("/{path:path}", methods=["GET"])
async def webdav_get(
    path: str,
    project: ProjectPathDep,
    project_service: ProjectServiceDep,
):
    """WebDAV GET endpoint."""
    project_path = await get_project_path_or_404(project_service, project)
    file_path = await get_project_file_path_or_404(project_path, path)
    return await _webdav_get(file_path)


@router.api_route("/{path:path}", methods=["PUT"])
async def webdav_put(
    request: Request,
    path: str,
    project: ProjectPathDep,
    project_service: ProjectServiceDep,
):
    """WebDAV PUT endpoint."""
    project_path = await get_project_path_or_404(project_service, project)
    file_path = Path(project_path / path)
    return await _webdav_put(request, project, file_path)


@router.api_route("/{path:path}", methods=["DELETE"])
async def webdav_delete(
    path: str,
    project: ProjectPathDep,
    project_service: ProjectServiceDep,
):
    """WebDAV DELETE endpoint."""
    project_path = await get_project_path_or_404(project_service, project)
    file_path = await get_project_file_path_or_404(project_path, path)
    return await _webdav_delete(project, file_path)


@router.api_route("/{path:path}", methods=["MKCOL"])
async def webdav_mkcol(
    path: str,
    project: ProjectPathDep,
    project_service: ProjectServiceDep,
):
    """WebDAV MKCOL endpoint."""
    project_path = await get_project_path_or_404(project_service, project)
    dir_path = Path(project_path / path)
    return await _webdav_mkcol(project, dir_path)


# Handle WebDAV root
@router.api_route("/", methods=["OPTIONS", "PROPFIND"])
async def webdav_root(
    request: Request,
    project: ProjectPathDep,
    project_service: ProjectServiceDep,
):
    """WebDAV root endpoint."""
    project_path = await get_project_path_or_404(project_service, project)
    method = request.method
    if method == "OPTIONS":
        return await _webdav_options(project_path)
    else:
        return await _webdav_propfind(project, project_path, project_path)


async def _webdav_options(file_path: Path) -> Response:
    """Handle WebDAV OPTIONS request."""
    file_size = file_path.stat().st_size
    return Response(
        status_code=204,
        headers={
            "DAV": "1,2",
            "MS-Author-Via": "DAV",
            "Allow": "OPTIONS,GET,HEAD,POST,DELETE,TRACE,PROPFIND,PROPPATCH,COPY,MOVE,LOCK,UNLOCK,PUT",
            "Content-Length": f"{file_size}",
        },
    )


async def _webdav_propfind(project: str, project_path: Path, file_path: Path) -> Response:
    """Handle WebDAV PROPFIND request to list directory contents."""

    # Calculate relative path from project root
    try:
        relative_path = file_path.relative_to(project_path)
        relative_path_str = str(relative_path).replace("\\", "/")
        if relative_path_str == ".":
            relative_path_str = ""
    except ValueError:
        # file_path is not under project_path
        relative_path_str = ""

    # Build minimal PROPFIND response
    if file_path.is_dir():
        # Directory listing
        href_path = (
            f"/{project}/webdav/{relative_path_str}/"
            if relative_path_str
            else f"/{project}/webdav/"
        )
        xml_response = f"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
<D:response>
    <D:href>{href_path}</D:href>
    <D:propstat>
        <D:prop>
            <D:resourcetype><D:collection/></D:resourcetype>
            <D:displayname>{file_path.name if file_path.name else project}</D:displayname>
        </D:prop>
        <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
</D:response>
"""

        # Add child items
        for child in file_path.iterdir():
            # Calculate child relative path
            child_relative = child.relative_to(project_path)
            child_relative_str = str(child_relative).replace("\\", "/")

            if child.is_dir():
                child_href = f"/{project}/webdav/{child_relative_str}/"
                xml_response += f"""<D:response>
    <D:href>{child_href}</D:href>
    <D:propstat>
        <D:prop>
            <D:resourcetype><D:collection/></D:resourcetype>
            <D:displayname>{child.name}</D:displayname>
        </D:prop>
        <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
</D:response>
"""
            else:
                child_href = f"/{project}/webdav/{child_relative_str}"
                file_size = child.stat().st_size
                xml_response += f"""<D:response>
    <D:href>{child_href}</D:href>
    <D:propstat>
        <D:prop>
            <D:resourcetype/>
            <D:displayname>{child.name}</D:displayname>
            <D:getcontentlength>{file_size}</D:getcontentlength>
        </D:prop>
        <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
</D:response>
"""

        xml_response += "</D:multistatus>"
    else:
        # File properties
        href_path = f"/{project}/webdav/{relative_path_str}"
        file_size = file_path.stat().st_size
        xml_response = f"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
<D:response>
    <D:href>{href_path}</D:href>
    <D:propstat>
        <D:prop>
            <D:resourcetype/>
            <D:displayname>{file_path.name}</D:displayname>
            <D:getcontentlength>{file_size}</D:getcontentlength>
        </D:prop>
        <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
</D:response>
</D:multistatus>"""

    return Response(content=xml_response, status_code=207, media_type="text/xml; charset=utf-8")


async def _webdav_get(file_path: Path) -> Response:
    """Handle WebDAV GET request to download file."""

    async def file_generator():
        async with aiofiles.open(file_path, "rb") as file:
            while chunk := await file.read(8192):
                yield chunk

    file_size = file_path.stat().st_size
    headers = {"Content-Length": str(file_size), "Content-Type": "application/octet-stream"}

    return StreamingResponse(file_generator(), status_code=200, headers=headers)


async def _webdav_put(request: Request, project: str, file_path: Path) -> Response:
    """Handle WebDAV PUT request to upload file."""

    # Check if file exists before writing (for correct HTTP status)
    file_existed = file_path.exists()

    # Ensure parent directory exists
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # Write file content
    try:
        async with aiofiles.open(file_path, "wb") as file:
            async for chunk in request.stream():
                await file.write(chunk)

        # Preserve timestamps if provided in headers
        await _preserve_file_timestamps(request, file_path)

        logger.info(f"WebDAV: Uploaded file {file_path} to project {project}.")

        return Response(status_code=204 if file_existed else 201)

    except Exception as e:
        logger.error(f"WebDAV: Failed to upload file {file_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {e}") from e


async def _preserve_file_timestamps(request: Request, file_path: Path) -> None:
    """Preserve file timestamps from WebDAV headers if provided.

    Supports multiple timestamp header formats:
    - X-OC-Mtime: Unix timestamp (ownCloud/Nextcloud format)
    - X-Timestamp: Unix timestamp
    - X-Mtime: Unix timestamp
    - Last-Modified: HTTP date format
    """

    # Try different header formats for modification time
    mtime_timestamp = None

    # Check for custom timestamp headers (Unix timestamp)
    for header_name in ["X-OC-Mtime", "X-Timestamp", "X-Mtime"]:
        if header_name in request.headers:
            try:
                mtime_timestamp = float(request.headers[header_name])
                logger.debug(f"Using {header_name} timestamp: {mtime_timestamp}")
                break
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid timestamp in {header_name} header: {e}")
                continue

    # Fall back to Last-Modified header if no custom timestamp found
    if mtime_timestamp is None and "Last-Modified" in request.headers:
        try:
            # Parse HTTP date format
            last_modified_str = request.headers["Last-Modified"]
            dt = datetime.strptime(last_modified_str, "%a, %d %b %Y %H:%M:%S GMT")
            # Replace with UTC timezone to ensure correct timestamp calculation
            from datetime import timezone

            dt = dt.replace(tzinfo=timezone.utc)
            mtime_timestamp = dt.timestamp()
            logger.debug(f"Using Last-Modified timestamp: {mtime_timestamp}")
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid Last-Modified header format: {e}")

    # Apply timestamp if we found one
    if mtime_timestamp is not None:
        try:
            # Use os.utime to set both access and modification times
            # Set access time to modification time to keep them consistent
            os.utime(file_path, (mtime_timestamp, mtime_timestamp))
            logger.debug(f"Set file timestamps for {file_path} to {mtime_timestamp}")
        except OSError as e:
            logger.warning(f"Failed to set timestamps for {file_path}: {e}")
    else:
        logger.debug(f"No timestamp headers found, using current time for {file_path}")


async def _webdav_delete(project: str, file_path: Path) -> Response:
    """Handle WebDAV DELETE request to delete file or directory."""

    try:
        if file_path.is_dir():
            shutil.rmtree(file_path)
        else:
            file_path.unlink()

        logger.info(f"WebDAV: Deleted {file_path} for project {project}")
        return Response(status_code=204)

    except Exception as e:
        logger.error(f"WebDAV: Failed to delete {file_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete: {e}") from e


async def _webdav_mkcol(project: str, dir_path: Path) -> Response:
    """Handle WebDAV MKCOL request to create directory."""

    if dir_path.exists():
        raise HTTPException(status_code=405, detail="Directory already exists")

    try:
        dir_path.mkdir(parents=True, exist_ok=False)
        logger.info(f"WebDAV: Created directory {dir_path} for project {project}")
        return Response(status_code=201)

    except Exception as e:
        logger.error(f"WebDAV: Failed to create directory {dir_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create directory: {e}") from e
