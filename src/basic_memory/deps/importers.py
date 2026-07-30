"""Importer dependency injection for basic-memory.

This module provides importer dependencies:
- ChatGPTImporter
- ClaudeConversationsImporter
- ClaudeProjectsImporter
- MemoryJsonImporter
"""

from typing import Annotated

from fastapi import Depends

from basic_memory.deps.projects import ProjectConfigV2ExternalDep
from basic_memory.deps.read_cache import ReadCacheDep
from basic_memory.deps.services import (
    FileServiceV2ExternalDep,
    MarkdownProcessorV2ExternalDep,
)
from basic_memory.importers import (
    ChatGPTImporter,
    ClaudeConversationsImporter,
    ClaudeProjectsImporter,
    ImportReadCache,
    MemoryJsonImporter,
)


async def get_import_read_cache(
    read_cache: ReadCacheDep,
    project_id: str,
) -> ImportReadCache | None:
    """Bind the optional host cache to the requested project."""
    if read_cache is None:
        return None
    return ImportReadCache(backend=read_cache, project_id=project_id)


ImportReadCacheDep = Annotated[ImportReadCache | None, Depends(get_import_read_cache)]


# --- ChatGPT Importer ---


async def get_chatgpt_importer_v2_external(
    project_config: ProjectConfigV2ExternalDep,
    markdown_processor: MarkdownProcessorV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
    read_cache: ImportReadCacheDep,
) -> ChatGPTImporter:
    """Create ChatGPTImporter with v2 external_id dependencies."""
    return ChatGPTImporter(
        project_config.home,
        markdown_processor,
        file_service,
        project_name=project_config.name,
        read_cache=read_cache,
    )


ChatGPTImporterV2ExternalDep = Annotated[ChatGPTImporter, Depends(get_chatgpt_importer_v2_external)]


# --- Claude Conversations Importer ---


async def get_claude_conversations_importer_v2_external(
    project_config: ProjectConfigV2ExternalDep,
    markdown_processor: MarkdownProcessorV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
    read_cache: ImportReadCacheDep,
) -> ClaudeConversationsImporter:
    """Create ClaudeConversationsImporter with v2 external_id dependencies."""
    return ClaudeConversationsImporter(
        project_config.home,
        markdown_processor,
        file_service,
        project_name=project_config.name,
        read_cache=read_cache,
    )


ClaudeConversationsImporterV2ExternalDep = Annotated[
    ClaudeConversationsImporter, Depends(get_claude_conversations_importer_v2_external)
]


# --- Claude Projects Importer ---


async def get_claude_projects_importer_v2_external(
    project_config: ProjectConfigV2ExternalDep,
    markdown_processor: MarkdownProcessorV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
    read_cache: ImportReadCacheDep,
) -> ClaudeProjectsImporter:
    """Create ClaudeProjectsImporter with v2 external_id dependencies."""
    return ClaudeProjectsImporter(
        project_config.home,
        markdown_processor,
        file_service,
        project_name=project_config.name,
        read_cache=read_cache,
    )


ClaudeProjectsImporterV2ExternalDep = Annotated[
    ClaudeProjectsImporter, Depends(get_claude_projects_importer_v2_external)
]


# --- Memory JSON Importer ---


async def get_memory_json_importer_v2_external(
    project_config: ProjectConfigV2ExternalDep,
    markdown_processor: MarkdownProcessorV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
    read_cache: ImportReadCacheDep,
) -> MemoryJsonImporter:
    """Create MemoryJsonImporter with v2 external_id dependencies."""
    return MemoryJsonImporter(
        project_config.home,
        markdown_processor,
        file_service,
        project_name=project_config.name,
        read_cache=read_cache,
    )


MemoryJsonImporterV2ExternalDep = Annotated[
    MemoryJsonImporter, Depends(get_memory_json_importer_v2_external)
]
