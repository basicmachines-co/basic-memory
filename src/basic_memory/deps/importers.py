"""Importer dependency injection for basic-memory.

This module provides importer dependencies:
- ChatGPTImporter
- ClaudeConversationsImporter
- ClaudeProjectsImporter
- MemoryJsonImporter
"""

from typing import Annotated

from fastapi import Depends

from basic_memory.deps.projects import (
    ProjectConfigDep,
    ProjectConfigV2Dep,
    ProjectConfigV2ExternalDep,
)
from basic_memory.deps.services import (
    FileServiceDep,
    FileServiceV2Dep,
    FileServiceV2ExternalDep,
    MarkdownProcessorDep,
    MarkdownProcessorV2Dep,
    MarkdownProcessorV2ExternalDep,
)
from basic_memory.importers import (
    ChatGPTImporter,
    ClaudeConversationsImporter,
    ClaudeProjectsImporter,
    MemoryJsonImporter,
)


# --- ChatGPT Importer ---


async def get_chatgpt_importer(
    project_config: ProjectConfigDep,
    markdown_processor: MarkdownProcessorDep,
    file_service: FileServiceDep,
) -> ChatGPTImporter:
    """Create ChatGPTImporter with dependencies."""
    return ChatGPTImporter(project_config.home, markdown_processor, file_service)


ChatGPTImporterDep = Annotated[ChatGPTImporter, Depends(get_chatgpt_importer)]


async def get_chatgpt_importer_v2(  # pragma: no cover
    project_config: ProjectConfigV2Dep,
    markdown_processor: MarkdownProcessorV2Dep,
    file_service: FileServiceV2Dep,
) -> ChatGPTImporter:
    """Create ChatGPTImporter with v2 dependencies."""
    return ChatGPTImporter(project_config.home, markdown_processor, file_service)


ChatGPTImporterV2Dep = Annotated[ChatGPTImporter, Depends(get_chatgpt_importer_v2)]


async def get_chatgpt_importer_v2_external(
    project_config: ProjectConfigV2ExternalDep,
    markdown_processor: MarkdownProcessorV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
) -> ChatGPTImporter:
    """Create ChatGPTImporter with v2 external_id dependencies."""
    return ChatGPTImporter(project_config.home, markdown_processor, file_service)


ChatGPTImporterV2ExternalDep = Annotated[ChatGPTImporter, Depends(get_chatgpt_importer_v2_external)]


# --- Claude Conversations Importer ---


async def get_claude_conversations_importer(
    project_config: ProjectConfigDep,
    markdown_processor: MarkdownProcessorDep,
    file_service: FileServiceDep,
) -> ClaudeConversationsImporter:
    """Create ClaudeConversationsImporter with dependencies."""
    return ClaudeConversationsImporter(project_config.home, markdown_processor, file_service)


ClaudeConversationsImporterDep = Annotated[
    ClaudeConversationsImporter, Depends(get_claude_conversations_importer)
]


async def get_claude_conversations_importer_v2(  # pragma: no cover
    project_config: ProjectConfigV2Dep,
    markdown_processor: MarkdownProcessorV2Dep,
    file_service: FileServiceV2Dep,
) -> ClaudeConversationsImporter:
    """Create ClaudeConversationsImporter with v2 dependencies."""
    return ClaudeConversationsImporter(project_config.home, markdown_processor, file_service)


ClaudeConversationsImporterV2Dep = Annotated[
    ClaudeConversationsImporter, Depends(get_claude_conversations_importer_v2)
]


async def get_claude_conversations_importer_v2_external(
    project_config: ProjectConfigV2ExternalDep,
    markdown_processor: MarkdownProcessorV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
) -> ClaudeConversationsImporter:
    """Create ClaudeConversationsImporter with v2 external_id dependencies."""
    return ClaudeConversationsImporter(project_config.home, markdown_processor, file_service)


ClaudeConversationsImporterV2ExternalDep = Annotated[
    ClaudeConversationsImporter, Depends(get_claude_conversations_importer_v2_external)
]


# --- Claude Projects Importer ---


async def get_claude_projects_importer(
    project_config: ProjectConfigDep,
    markdown_processor: MarkdownProcessorDep,
    file_service: FileServiceDep,
) -> ClaudeProjectsImporter:
    """Create ClaudeProjectsImporter with dependencies."""
    return ClaudeProjectsImporter(project_config.home, markdown_processor, file_service)


ClaudeProjectsImporterDep = Annotated[ClaudeProjectsImporter, Depends(get_claude_projects_importer)]


async def get_claude_projects_importer_v2(  # pragma: no cover
    project_config: ProjectConfigV2Dep,
    markdown_processor: MarkdownProcessorV2Dep,
    file_service: FileServiceV2Dep,
) -> ClaudeProjectsImporter:
    """Create ClaudeProjectsImporter with v2 dependencies."""
    return ClaudeProjectsImporter(project_config.home, markdown_processor, file_service)


ClaudeProjectsImporterV2Dep = Annotated[
    ClaudeProjectsImporter, Depends(get_claude_projects_importer_v2)
]


async def get_claude_projects_importer_v2_external(
    project_config: ProjectConfigV2ExternalDep,
    markdown_processor: MarkdownProcessorV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
) -> ClaudeProjectsImporter:
    """Create ClaudeProjectsImporter with v2 external_id dependencies."""
    return ClaudeProjectsImporter(project_config.home, markdown_processor, file_service)


ClaudeProjectsImporterV2ExternalDep = Annotated[
    ClaudeProjectsImporter, Depends(get_claude_projects_importer_v2_external)
]


# --- Memory JSON Importer ---


async def get_memory_json_importer(
    project_config: ProjectConfigDep,
    markdown_processor: MarkdownProcessorDep,
    file_service: FileServiceDep,
) -> MemoryJsonImporter:
    """Create MemoryJsonImporter with dependencies."""
    return MemoryJsonImporter(project_config.home, markdown_processor, file_service)


MemoryJsonImporterDep = Annotated[MemoryJsonImporter, Depends(get_memory_json_importer)]


async def get_memory_json_importer_v2(  # pragma: no cover
    project_config: ProjectConfigV2Dep,
    markdown_processor: MarkdownProcessorV2Dep,
    file_service: FileServiceV2Dep,
) -> MemoryJsonImporter:
    """Create MemoryJsonImporter with v2 dependencies."""
    return MemoryJsonImporter(project_config.home, markdown_processor, file_service)


MemoryJsonImporterV2Dep = Annotated[MemoryJsonImporter, Depends(get_memory_json_importer_v2)]


async def get_memory_json_importer_v2_external(
    project_config: ProjectConfigV2ExternalDep,
    markdown_processor: MarkdownProcessorV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
) -> MemoryJsonImporter:
    """Create MemoryJsonImporter with v2 external_id dependencies."""
    return MemoryJsonImporter(project_config.home, markdown_processor, file_service)


MemoryJsonImporterV2ExternalDep = Annotated[
    MemoryJsonImporter, Depends(get_memory_json_importer_v2_external)
]
