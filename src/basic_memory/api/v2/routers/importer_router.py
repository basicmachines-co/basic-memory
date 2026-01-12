"""V2 Import Router - ID-based data import operations.

This router uses v2 dependencies for consistent project handling with external_id UUIDs.
Import endpoints use project_id in the path for consistency with other v2 endpoints.
"""

import json
import logging

from fastapi import APIRouter, Form, HTTPException, UploadFile, status, Path

from basic_memory.deps import (
    ChatGPTImporterV2ExternalDep,
    ClaudeConversationsImporterV2ExternalDep,
    ClaudeProjectsImporterV2ExternalDep,
    MemoryJsonImporterV2ExternalDep,
)
from basic_memory.importers import Importer
from basic_memory.schemas.importer import (
    ChatImportResult,
    EntityImportResult,
    ProjectImportResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/import", tags=["import-v2"])


@router.post("/chatgpt", response_model=ChatImportResult)
async def import_chatgpt(
    importer: ChatGPTImporterV2ExternalDep,
    file: UploadFile,
    project_id: str = Path(..., description="Project external UUID"),
    folder: str = Form("conversations"),
) -> ChatImportResult:
    """Import conversations from ChatGPT JSON export.

    Args:
        project_id: Project external UUID from URL path
        file: The ChatGPT conversations.json file.
        folder: The folder to place the files in.
        importer: ChatGPT importer instance.

    Returns:
        ChatImportResult with import statistics.

    Raises:
        HTTPException: If import fails.
    """
    logger.info(f"V2 Importing ChatGPT conversations for project {project_id}")
    return await import_file(importer, file, folder)


@router.post("/claude/conversations", response_model=ChatImportResult)
async def import_claude_conversations(
    importer: ClaudeConversationsImporterV2ExternalDep,
    file: UploadFile,
    project_id: str = Path(..., description="Project external UUID"),
    folder: str = Form("conversations"),
) -> ChatImportResult:
    """Import conversations from Claude conversations.json export.

    Args:
        project_id: Project external UUID from URL path
        file: The Claude conversations.json file.
        folder: The folder to place the files in.
        importer: Claude conversations importer instance.

    Returns:
        ChatImportResult with import statistics.

    Raises:
        HTTPException: If import fails.
    """
    logger.info(f"V2 Importing Claude conversations for project {project_id}")
    return await import_file(importer, file, folder)


@router.post("/claude/projects", response_model=ProjectImportResult)
async def import_claude_projects(
    importer: ClaudeProjectsImporterV2ExternalDep,
    file: UploadFile,
    project_id: str = Path(..., description="Project external UUID"),
    folder: str = Form("projects"),
) -> ProjectImportResult:
    """Import projects from Claude projects.json export.

    Args:
        project_id: Project external UUID from URL path
        file: The Claude projects.json file.
        folder: The base folder to place the files in.
        importer: Claude projects importer instance.

    Returns:
        ProjectImportResult with import statistics.

    Raises:
        HTTPException: If import fails.
    """
    logger.info(f"V2 Importing Claude projects for project {project_id}")
    return await import_file(importer, file, folder)


@router.post("/memory-json", response_model=EntityImportResult)
async def import_memory_json(
    importer: MemoryJsonImporterV2ExternalDep,
    file: UploadFile,
    project_id: str = Path(..., description="Project external UUID"),
    folder: str = Form("conversations"),
) -> EntityImportResult:
    """Import entities and relations from a memory.json file.

    Args:
        project_id: Project external UUID from URL path
        file: The memory.json file.
        folder: Optional destination folder within the project.
        importer: Memory JSON importer instance.

    Returns:
        EntityImportResult with import statistics.

    Raises:
        HTTPException: If import fails.
    """
    logger.info(f"V2 Importing memory.json for project {project_id}")
    try:
        file_data = []
        file_bytes = await file.read()
        file_str = file_bytes.decode("utf-8")
        for line in file_str.splitlines():
            json_data = json.loads(line)
            file_data.append(json_data)

        result = await importer.import_data(file_data, folder)
        if not result.success:  # pragma: no cover
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=result.error_message or "Import failed",
            )
    except Exception as e:
        logger.exception("V2 Import failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {str(e)}",
        )
    return result


async def import_file(importer: Importer, file: UploadFile, destination_folder: str):
    """Helper function to import a file using an importer instance.

    Args:
        importer: The importer instance to use
        file: The file to import
        destination_folder: Destination folder for imported content

    Returns:
        Import result from the importer

    Raises:
        HTTPException: If import fails
    """
    try:
        # Process file
        json_data = json.load(file.file)
        result = await importer.import_data(json_data, destination_folder)
        if not result.success:  # pragma: no cover
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=result.error_message or "Import failed",
            )

        return result

    except Exception as e:
        logger.exception("V2 Import failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {str(e)}",
        )
