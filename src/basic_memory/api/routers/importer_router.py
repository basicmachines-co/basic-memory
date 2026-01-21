"""Import router for Basic Memory API."""

import json
import logging

from fastapi import APIRouter, Form, HTTPException, UploadFile, status

from basic_memory.deps import (
    ChatGPTImporterDep,
    ClaudeConversationsImporterDep,
    ClaudeProjectsImporterDep,
    MemoryJsonImporterDep,
)
from basic_memory.importers import Importer
from basic_memory.schemas.importer import (
    ChatImportResult,
    EntityImportResult,
    ProjectImportResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/import", tags=["import"])


@router.post("/chatgpt", response_model=ChatImportResult)
async def import_chatgpt(
    importer: ChatGPTImporterDep,
    file: UploadFile,
    directory: str = Form("conversations"),
) -> ChatImportResult:
    """Import conversations from ChatGPT JSON export.

    Args:
        file: The ChatGPT conversations.json file.
        directory: The directory to place the files in.
        markdown_processor: MarkdownProcessor instance.

    Returns:
        ChatImportResult with import statistics.

    Raises:
        HTTPException: If import fails.
    """
    return await import_file(importer, file, directory)


@router.post("/claude/conversations", response_model=ChatImportResult)
async def import_claude_conversations(
    importer: ClaudeConversationsImporterDep,
    file: UploadFile,
    directory: str = Form("conversations"),
) -> ChatImportResult:
    """Import conversations from Claude conversations.json export.

    Args:
        file: The Claude conversations.json file.
        directory: The directory to place the files in.
        markdown_processor: MarkdownProcessor instance.

    Returns:
        ChatImportResult with import statistics.

    Raises:
        HTTPException: If import fails.
    """
    return await import_file(importer, file, directory)


@router.post("/claude/projects", response_model=ProjectImportResult)
async def import_claude_projects(
    importer: ClaudeProjectsImporterDep,
    file: UploadFile,
    directory: str = Form("projects"),
) -> ProjectImportResult:
    """Import projects from Claude projects.json export.

    Args:
        file: The Claude projects.json file.
        directory: The directory to place the files in.
        markdown_processor: MarkdownProcessor instance.

    Returns:
        ProjectImportResult with import statistics.

    Raises:
        HTTPException: If import fails.
    """
    return await import_file(importer, file, directory)


@router.post("/memory-json", response_model=EntityImportResult)
async def import_memory_json(
    importer: MemoryJsonImporterDep,
    file: UploadFile,
    directory: str = Form("conversations"),
) -> EntityImportResult:
    """Import entities and relations from a memory.json file.

    Args:
        file: The memory.json file.
        directory: Optional destination directory within the project.
        markdown_processor: MarkdownProcessor instance.

    Returns:
        EntityImportResult with import statistics.

    Raises:
        HTTPException: If import fails.
    """
    try:
        file_data = []
        file_bytes = await file.read()
        file_str = file_bytes.decode("utf-8")
        for line in file_str.splitlines():
            json_data = json.loads(line)
            file_data.append(json_data)

        result = await importer.import_data(file_data, directory)
        if not result.success:  # pragma: no cover
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=result.error_message or "Import failed",
            )
    except Exception as e:
        logger.exception("Import failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {str(e)}",
        )
    return result


async def import_file(importer: Importer, file: UploadFile, destination_folder: str):
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
        logger.exception("Import failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {str(e)}",
        )
