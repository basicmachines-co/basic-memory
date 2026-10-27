"""Schemas for import services."""

from typing import Dict, Optional

from pydantic import BaseModel


class ImportResult(BaseModel):
    """Common import result schema."""

    import_count: Dict[str, int]
    success: bool
    error_message: Optional[str] = None


class ChatImportResult(ImportResult):
    """Result schema for chat imports."""

    conversations: int = 0
    messages: int = 0
    # Callers that act on the imported notes (an index note, a follow-up agent run)
    # need to know which files the import wrote; the counts alone do not say.
    file_paths: list[str] = []


class ProjectImportResult(ImportResult):
    """Result schema for project imports."""

    documents: int = 0
    prompts: int = 0


class EntityImportResult(ImportResult):
    """Result schema for entity imports."""

    entities: int = 0
    relations: int = 0
    skipped_entities: int = 0
