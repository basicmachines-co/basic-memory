"""V2 Import Router - ID-based data import operations.

This router uses v2 dependencies for consistent project handling with external_id UUIDs.
Import endpoints use project_id in the path for consistency with other v2 endpoints.
"""

import json
import logging
from contextlib import nullcontext

from fastapi import APIRouter, Form, HTTPException, Path, UploadFile, status

from basic_memory.deps import (
    AppConfigDep,
    ChatGPTImporterV2ExternalDep,
    ClaudeConversationsImporterV2ExternalDep,
    ClaudeProjectsImporterV2ExternalDep,
    MemoryJsonImporterV2ExternalDep,
    ReadCacheDep,
)
from basic_memory.importers import Importer
from basic_memory.read_cache import ReadCache, invalidate_cache
from basic_memory.schemas.importer import (
    ChatImportResult,
    EntityImportResult,
    ImportResult,
    ProjectImportResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/import", tags=["import-v2"])


async def read_import_upload(file: UploadFile, max_bytes: int) -> bytes:
    """Read an import upload with a hard cap before JSON parsing."""
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Import file exceeds maximum size of {max_bytes} bytes.",
        )
    return content


@router.post("/chatgpt", response_model=ChatImportResult)
async def import_chatgpt(
    importer: ChatGPTImporterV2ExternalDep,
    config: AppConfigDep,
    file: UploadFile,
    read_cache: ReadCacheDep,
    project_id: str = Path(..., description="Project external UUID"),
    directory: str = Form("conversations"),
) -> ChatImportResult:
    """Import conversations from ChatGPT JSON export.

    Args:
        project_id: Project external UUID from URL path
        file: The ChatGPT conversations.json file.
        directory: The directory to place the files in.
        importer: ChatGPT importer instance.

    Returns:
        ChatImportResult with import statistics.

    Raises:
        HTTPException: If import fails.
    """
    logger.info(f"V2 Importing ChatGPT conversations for project {project_id}")
    return await import_file(
        importer,
        file,
        directory,
        config.import_upload_max_bytes,
        read_cache=read_cache,
        project_external_id=project_id,
    )


@router.post("/claude/conversations", response_model=ChatImportResult)
async def import_claude_conversations(
    importer: ClaudeConversationsImporterV2ExternalDep,
    config: AppConfigDep,
    file: UploadFile,
    read_cache: ReadCacheDep,
    project_id: str = Path(..., description="Project external UUID"),
    directory: str = Form("conversations"),
) -> ChatImportResult:
    """Import conversations from Claude conversations.json export.

    Args:
        project_id: Project external UUID from URL path
        file: The Claude conversations.json file.
        directory: The directory to place the files in.
        importer: Claude conversations importer instance.

    Returns:
        ChatImportResult with import statistics.

    Raises:
        HTTPException: If import fails.
    """
    logger.info(f"V2 Importing Claude conversations for project {project_id}")
    return await import_file(
        importer,
        file,
        directory,
        config.import_upload_max_bytes,
        read_cache=read_cache,
        project_external_id=project_id,
    )


@router.post("/claude/projects", response_model=ProjectImportResult)
async def import_claude_projects(
    importer: ClaudeProjectsImporterV2ExternalDep,
    config: AppConfigDep,
    file: UploadFile,
    read_cache: ReadCacheDep,
    project_id: str = Path(..., description="Project external UUID"),
    directory: str = Form("projects"),
) -> ProjectImportResult:
    """Import projects from Claude projects.json export.

    Args:
        project_id: Project external UUID from URL path
        file: The Claude projects.json file.
        directory: The base directory to place the files in.
        importer: Claude projects importer instance.

    Returns:
        ProjectImportResult with import statistics.

    Raises:
        HTTPException: If import fails.
    """
    logger.info(f"V2 Importing Claude projects for project {project_id}")
    return await import_file(
        importer,
        file,
        directory,
        config.import_upload_max_bytes,
        read_cache=read_cache,
        project_external_id=project_id,
    )


@router.post("/memory-json", response_model=EntityImportResult)
async def import_memory_json(
    importer: MemoryJsonImporterV2ExternalDep,
    config: AppConfigDep,
    file: UploadFile,
    read_cache: ReadCacheDep,
    project_id: str = Path(..., description="Project external UUID"),
    directory: str = Form("conversations"),
) -> EntityImportResult:
    """Import entities and relations from a memory.json file.

    Args:
        project_id: Project external UUID from URL path
        file: The memory.json file.
        directory: Optional destination directory within the project.
        importer: Memory JSON importer instance.

    Returns:
        EntityImportResult with import statistics.

    Raises:
        HTTPException: If import fails.
    """
    logger.info(f"V2 Importing memory.json for project {project_id}")
    try:
        file_data = []
        file_bytes = await read_import_upload(file, config.import_upload_max_bytes)
        file_str = file_bytes.decode("utf-8")
        for line in file_str.splitlines():
            json_data = json.loads(line)
            file_data.append(json_data)

        result = await run_import_with_invalidation(
            importer,
            file_data,
            directory,
            read_cache=read_cache,
            project_external_id=project_id,
        )
        if not result.success:  # pragma: no cover
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=result.error_message or "Import failed",
            )
    except HTTPException:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        # Trigger: a line in the upload is not valid JSON, or the bytes are not
        #   UTF-8 (truncated archive, wrong file, binary upload).
        # Why: this is a client-input problem, not a server fault (#1276).
        # Outcome: a 400 with the parse position instead of an opaque 500.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Import file is not valid JSON: {e}",
        )
    except Exception as e:
        logger.exception("V2 Import failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {str(e)}",
        )
    return result


async def import_file[ImportResultT: ImportResult](
    importer: Importer[ImportResultT],
    file: UploadFile,
    destination_directory: str,
    max_bytes: int,
    *,
    read_cache: ReadCache | None,
    project_external_id: str,
) -> ImportResultT:
    """Helper function to import a file using an importer instance.

    Args:
        importer: The importer instance to use
        file: The file to import
        destination_directory: Destination directory for imported content
        max_bytes: Maximum upload size in bytes; raises HTTP 413 if exceeded

    Returns:
        Import result from the importer

    Raises:
        HTTPException: If import fails
    """
    try:
        # Process file
        upload_bytes = await read_import_upload(file, max_bytes)
        json_data = json.loads(upload_bytes)
        result = await run_import_with_invalidation(
            importer,
            json_data,
            destination_directory,
            read_cache=read_cache,
            project_external_id=project_external_id,
        )
        if not result.success:  # pragma: no cover
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=result.error_message or "Import failed",
            )

        return result

    except HTTPException:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        # Trigger: the upload is not valid JSON, or the bytes are not UTF-8
        #   (truncated archive, wrong file, binary upload).
        # Why: this is a client-input problem, not a server fault (#1276).
        # Outcome: a 400 with the parse position instead of an opaque 500.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Import file is not valid JSON: {e}",
        )
    except Exception as e:
        logger.exception("V2 Import failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {str(e)}",
        )


async def run_import_with_invalidation[ImportResultT: ImportResult](
    importer: Importer[ImportResultT],
    source_data: object,
    destination_directory: str,
    *,
    read_cache: ReadCache | None,
    project_external_id: str,
) -> ImportResultT:
    """Run one import attempt and invalidate files it may have written."""
    # Importers write files one at a time and may return a failed result after
    # earlier writes. Invalidate every attempted import so cached file-first
    # resources cannot survive either success or partial failure.
    invalidation_scope = (
        invalidate_cache(read_cache, project_external_id)
        if read_cache is not None
        else nullcontext()
    )
    async with invalidation_scope:
        return await importer.import_data(source_data, destination_directory)
